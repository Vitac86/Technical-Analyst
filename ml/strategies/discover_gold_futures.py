"""
Discover gold futures candidates on BCS.

This script queries the BCS information service for FUTURES (and optionally
GOODS) instruments and filters them down to plausible gold futures by ticker
and by display/short name. It is intentionally lenient — the goal is to
collect *candidates* for the availability scan, not to commit to a single
ticker shape.

Auth
----
Reads credentials from environment:

* ``BCS_REFRESH_TOKEN``     refresh token issued by BCS (read-only required)
* ``BCS_CLIENT_ID``         client id, defaults to ``trade-api-read``

Tokens are NEVER printed or written to disk.

Endpoints
---------
By type        : GET https://be.broker.ru/trade-api-information-service
                       /api/v1/instruments/by-type?type=FUTURES&size=50&page=0
By base asset  : GET .../api/v1/instruments/by-type?type=FUTURES
                       &size=50&page=0&baseAssetTicker=GOLD  (optional probe)

Filters
-------
A candidate is kept if any of the following is true:

* ticker contains one of: GOLD, GLD, AU, GDA, GD, GOLDRUB
* displayName / shortName matches gold/golden/au/zoloto (case-insensitive)
* baseAssetTicker matches one of the gold tickers

The instrument type must be ``FUTURES`` (we still check GOODS as a fallback
to surface anything BCS might classify oddly).

Outputs
-------
ml/reports/strategies/gold_futures_discovery.json
ml/reports/strategies/gold_futures_discovery.csv

Both gitignored (ml/reports/ is in .gitignore).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Allow running this file as a script.
_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ml.instruments.bcs_instruments_by_type import (  # noqa: E402
    BCS_INSTRUMENTS_BY_TYPE_SIZE_PARAM,
    BcsError,
    fetch_instruments_by_type,
    get_access_token,
)


REPORT_DIR = _REPO_ROOT / "ml" / "reports" / "strategies"

GOLD_TICKER_TOKENS = ("GOLD", "GLD", "GOLDRUB", "GDA", "GDC", "GD")
GOLD_NAME_PATTERNS = (
    re.compile(r"\bgold(en)?\b", re.IGNORECASE),
    re.compile(r"\bзолот", re.IGNORECASE),
    re.compile(r"\bau\b", re.IGNORECASE),
)
DEFAULT_BASE_ASSET_PROBES = ("GOLD", "GLD", "AU")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


@dataclass
class Candidate:
    ticker: str
    classCode: str | None
    displayName: str | None
    shortName: str | None
    instrumentType: str | None
    baseAssetTicker: str | None
    maturityDate: str | None
    expirationDate: str | None
    settlementDate: str | None
    tradingCurrency: str | None
    lotSize: float | int | None
    minimumStep: float | int | None
    boards: list[Any] = field(default_factory=list)
    source: str = ""
    match_reason: str = ""


def _str_contains_any(value: str | None, tokens: tuple[str, ...]) -> str | None:
    if not value:
        return None
    upper = value.upper()
    for token in tokens:
        if token in upper:
            return token
    return None


def _name_matches_gold(value: str | None) -> str | None:
    if not value:
        return None
    for pattern in GOLD_NAME_PATTERNS:
        m = pattern.search(value)
        if m:
            return m.group(0)
    return None


def _looks_like_gold(instrument: dict[str, Any], raw: dict[str, Any] | None) -> str | None:
    """Return a short human-readable reason or None."""
    ticker = instrument.get("ticker")
    base_asset = None
    if isinstance(raw, dict):
        for key in ("baseAssetTicker", "underlyingTicker", "baseAsset", "underlying", "asset"):
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                base_asset = value.strip().upper()
                break

    hit = _str_contains_any(ticker, GOLD_TICKER_TOKENS)
    if hit:
        return f"ticker contains {hit}"
    if base_asset:
        ba_hit = _str_contains_any(base_asset, GOLD_TICKER_TOKENS)
        if ba_hit:
            return f"baseAssetTicker contains {ba_hit}"

    name_hit = _name_matches_gold(instrument.get("displayName")) or _name_matches_gold(
        instrument.get("shortName")
    )
    if name_hit:
        return f"name contains {name_hit!r}"

    return None


def _pick_date(raw: dict[str, Any] | None, keys: tuple[str, ...]) -> str | None:
    if not isinstance(raw, dict):
        return None
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)) and value:
            try:
                seconds = value / 1000.0 if value > 1_000_000_000_000 else value
                dt = datetime.fromtimestamp(seconds, tz=timezone.utc)
                return dt.isoformat(timespec="seconds").replace("+00:00", "Z")
            except (OSError, ValueError):
                continue
    return None


def _pick_base_asset(raw: dict[str, Any] | None) -> str | None:
    if not isinstance(raw, dict):
        return None
    for key in ("baseAssetTicker", "underlyingTicker", "baseAsset", "underlying"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
    return None


def to_candidate(
    instrument: dict[str, Any],
    *,
    source: str,
    match_reason: str,
) -> Candidate | None:
    ticker = instrument.get("ticker")
    if not ticker:
        return None
    raw = instrument.get("raw") if isinstance(instrument.get("raw"), dict) else None
    return Candidate(
        ticker=ticker,
        classCode=instrument.get("classCode"),
        displayName=instrument.get("displayName"),
        shortName=instrument.get("shortName"),
        instrumentType=instrument.get("instrumentType") or instrument.get("type"),
        baseAssetTicker=_pick_base_asset(raw),
        maturityDate=_pick_date(raw, ("maturityDate", "maturity", "expirationDate")),
        expirationDate=_pick_date(raw, ("expirationDate", "expireDate", "expiry")),
        settlementDate=_pick_date(raw, ("settlementDate", "settlement")),
        tradingCurrency=instrument.get("tradingCurrency"),
        lotSize=instrument.get("lotSize"),
        minimumStep=instrument.get("minimumStep"),
        boards=instrument.get("boards") or [],
        source=source,
        match_reason=match_reason,
    )


def filter_gold(
    instruments: list[dict[str, Any]],
    *,
    source: str,
) -> list[Candidate]:
    """Keep only plausible gold instruments."""
    candidates: list[Candidate] = []
    for inst in instruments:
        raw = inst.get("raw") if isinstance(inst.get("raw"), dict) else None
        reason = _looks_like_gold(inst, raw)
        if not reason:
            continue
        cand = to_candidate(inst, source=source, match_reason=reason)
        if cand is None:
            continue
        candidates.append(cand)
    return candidates


# ---------------------------------------------------------------------------
# Discovery orchestration
# ---------------------------------------------------------------------------


def _dedupe(candidates: list[Candidate]) -> list[Candidate]:
    seen: set[tuple[str, str | None]] = set()
    out: list[Candidate] = []
    for c in candidates:
        key = (c.ticker.upper(), (c.classCode or "").upper() or None)
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def discover(
    *,
    include_goods_fallback: bool = True,
    probe_base_asset: bool = True,
    page_size: int = 50,
    max_pages: int = 200,
) -> tuple[list[Candidate], list[dict[str, Any]]]:
    """Run discovery against BCS. Returns (candidates, source_log)."""
    access_token = get_access_token()

    sources_used: list[dict[str, Any]] = []
    candidates: list[Candidate] = []

    # Primary: FUTURES type with include_raw so we see maturity/baseAsset fields.
    futures_result = fetch_instruments_by_type(
        access_token,
        instrument_type="FUTURES",
        limit=page_size,
        max_pages=max_pages,
        include_raw=True,
        page_size_param=BCS_INSTRUMENTS_BY_TYPE_SIZE_PARAM,
    )
    sources_used.append(
        {
            "endpoint": futures_result.endpoint_url,
            "params": {"type": "FUTURES", "size": page_size},
            "total_returned": len(futures_result.instruments),
            "pages": futures_result.pages,
            "truncated": futures_result.truncated,
        }
    )
    candidates.extend(filter_gold(futures_result.instruments, source="by-type:FUTURES"))

    # Optional: GOODS fallback (BCS sometimes mis-classifies)
    if include_goods_fallback:
        try:
            goods_result = fetch_instruments_by_type(
                access_token,
                instrument_type="GOODS",
                limit=page_size,
                max_pages=max_pages,
                include_raw=True,
                page_size_param=BCS_INSTRUMENTS_BY_TYPE_SIZE_PARAM,
            )
            sources_used.append(
                {
                    "endpoint": goods_result.endpoint_url,
                    "params": {"type": "GOODS", "size": page_size},
                    "total_returned": len(goods_result.instruments),
                    "pages": goods_result.pages,
                    "truncated": goods_result.truncated,
                }
            )
            candidates.extend(filter_gold(goods_result.instruments, source="by-type:GOODS"))
        except BcsError as exc:
            sources_used.append(
                {
                    "endpoint": "by-type:GOODS",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    # Optional: baseAssetTicker probe. BCS docs document
    # by-type-and-base-asset-ticker; in practice the same endpoint accepts the
    # extra query parameter, so we re-use it and merge results.
    if probe_base_asset:
        for base_ticker in DEFAULT_BASE_ASSET_PROBES:
            try:
                probe = fetch_instruments_by_type(
                    access_token,
                    instrument_type="FUTURES",
                    limit=page_size,
                    max_pages=max_pages,
                    base_asset_ticker=base_ticker,
                    include_raw=True,
                    page_size_param=BCS_INSTRUMENTS_BY_TYPE_SIZE_PARAM,
                )
                sources_used.append(
                    {
                        "endpoint": probe.endpoint_url,
                        "params": {
                            "type": "FUTURES",
                            "baseAssetTicker": base_ticker,
                            "size": page_size,
                        },
                        "total_returned": len(probe.instruments),
                        "pages": probe.pages,
                        "truncated": probe.truncated,
                    }
                )
                # Anything returned for a gold base asset is by definition a hit.
                for inst in probe.instruments:
                    cand = to_candidate(
                        inst,
                        source=f"by-type:FUTURES?baseAssetTicker={base_ticker}",
                        match_reason=f"baseAssetTicker probe matched {base_ticker}",
                    )
                    if cand is not None:
                        candidates.append(cand)
            except ValueError:
                # fetch_instruments_by_type validates inputs; ignore probe-specific complaints.
                continue
            except BcsError as exc:
                sources_used.append(
                    {
                        "endpoint": "by-type:FUTURES (base asset probe)",
                        "params": {"baseAssetTicker": base_ticker},
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )

    return _dedupe(candidates), sources_used


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _candidate_to_dict(c: Candidate) -> dict[str, Any]:
    return {
        "ticker": c.ticker,
        "classCode": c.classCode,
        "displayName": c.displayName,
        "shortName": c.shortName,
        "instrumentType": c.instrumentType,
        "baseAssetTicker": c.baseAssetTicker,
        "maturityDate": c.maturityDate,
        "expirationDate": c.expirationDate,
        "settlementDate": c.settlementDate,
        "tradingCurrency": c.tradingCurrency,
        "lotSize": c.lotSize,
        "minimumStep": c.minimumStep,
        "boards": c.boards,
        "source": c.source,
        "match_reason": c.match_reason,
    }


def write_outputs(
    candidates: list[Candidate],
    sources_used: list[dict[str, Any]],
) -> tuple[Path, Path]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORT_DIR / "gold_futures_discovery.json"
    csv_path = REPORT_DIR / "gold_futures_discovery.csv"

    payload = {
        "generatedAt": utc_now_iso(),
        "totalCandidates": len(candidates),
        "sources": sources_used,
        "candidates": [_candidate_to_dict(c) for c in candidates],
    }
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )

    fieldnames = [
        "ticker", "classCode", "displayName", "shortName", "instrumentType",
        "baseAssetTicker", "maturityDate", "expirationDate", "settlementDate",
        "tradingCurrency", "lotSize", "minimumStep", "boards", "source",
        "match_reason",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for c in candidates:
            row = _candidate_to_dict(c)
            row["boards"] = json.dumps(row["boards"], ensure_ascii=False)
            writer.writerow(row)

    return json_path, csv_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Discover gold futures candidates on BCS.")
    p.add_argument("--page-size", type=int, default=50, help="BCS page size (default: 50).")
    p.add_argument("--max-pages", type=int, default=200, help="Pagination safety cap.")
    p.add_argument(
        "--skip-goods",
        action="store_true",
        help="Skip the GOODS fallback (only query FUTURES).",
    )
    p.add_argument(
        "--skip-base-asset-probe",
        action="store_true",
        help="Skip the baseAssetTicker probe (GOLD/GLD/AU).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_argparser().parse_args(argv)
    refresh_token = os.getenv("BCS_REFRESH_TOKEN", "").strip()
    if not refresh_token:
        print("BCS_REFRESH_TOKEN env var is not set.", file=sys.stderr)
        return 2

    try:
        candidates, sources = discover(
            include_goods_fallback=not args.skip_goods,
            probe_base_asset=not args.skip_base_asset_probe,
            page_size=args.page_size,
            max_pages=args.max_pages,
        )
    except BcsError as exc:
        print(f"BCS discovery failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3

    json_path, csv_path = write_outputs(candidates, sources)
    print(f"[discover-gold-futures] candidates: {len(candidates)}")
    for c in candidates[:20]:
        print(
            f"  - {c.ticker:<14} class={c.classCode or '?':<10} "
            f"type={c.instrumentType or '?':<10} {c.match_reason}"
        )
    if len(candidates) > 20:
        print(f"  ... ({len(candidates) - 20} more)")
    print(f"[discover-gold-futures] wrote {json_path.relative_to(_REPO_ROOT)}")
    print(f"[discover-gold-futures] wrote {csv_path.relative_to(_REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
