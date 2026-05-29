"""
Discover gold futures candidates on BCS, with a strict gold-only filter.

This script queries the BCS information service for FUTURES (and optionally
GOODS) instruments and accepts only candidates that are genuinely gold-related
Moscow-Exchange contracts. The default filter is intentionally narrow:

* ``classCode == "SPBFUT"`` (BCS classCode for MOEX futures)
* ticker matches a strong gold pattern:
    - ``GD<futures-month-code><year>`` (e.g. GDH7, GDM6, GDU6, GDZ6)
    - or ticker contains ``GLD`` / ``GOLD`` / ``GOLDRUB``
* or displayName / shortName contains ``gold`` / ``goldrub`` / ``золото``

A bare ``AU`` substring in the ticker is NOT enough on its own (this used to
let through unrelated futures like ``AAU6``, ``92M6``, ``95M6``, ``AAM6``).
Tickers like ``AU...`` will only pass when the instrument name also matches
the gold name patterns.

Auth
----
Reads credentials from environment:

* ``BCS_REFRESH_TOKEN``     refresh token issued by BCS (read-only required)
* ``BCS_CLIENT_ID``         client id, defaults to ``trade-api-read``

Tokens are NEVER printed or written to disk.

Endpoints
---------
GET https://be.broker.ru/trade-api-information-service/api/v1/instruments/by-type
    ?type=FUTURES&size=50&page=0
GET .../by-type?type=GOODS                                  (optional fallback)
GET .../by-type?type=FUTURES&baseAssetTicker=GOLD|GLD|AU     (probe)

Outputs
-------
ml/reports/strategies/gold_futures_discovery.json
ml/reports/strategies/gold_futures_discovery.csv            (accepted only)
ml/reports/strategies/gold_futures_discovery_rejected.csv   (diagnostics)
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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

# Strong ticker patterns.
# GD + futures month-code letter + 1-2 digit year (BCS / MOEX style).
# Month codes: F=Jan G=Feb H=Mar J=Apr K=May M=Jun N=Jul Q=Aug U=Sep V=Oct X=Nov Z=Dec
FUTURES_MONTH_CODES = "FGHJKMNQUVXZ"
STRICT_GD_TICKER_RE = re.compile(rf"^GD[{FUTURES_MONTH_CODES}]\d{{1,2}}$")
STRICT_TICKER_TOKENS = ("GOLDRUB", "GOLD", "GLD")  # checked left-to-right; longest first

# Strong name patterns (case-insensitive). Must match the literal word "gold"
# (optionally followed by "rub" / "ен" / etc.) or any "золот..." stem.
GOLD_NAME_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"gold(?:rub|en)?", re.IGNORECASE),
    re.compile(r"золот", re.IGNORECASE),
)

DEFAULT_BASE_ASSET_PROBES = ("GOLD", "GLD", "AU")
DEFAULT_CLASS_CODE = "SPBFUT"


# ---------------------------------------------------------------------------
# Filter
# ---------------------------------------------------------------------------


@dataclass
class FilterConfig:
    """Settings driving accept/reject for each instrument."""
    moex_only: bool = True
    class_code: str = DEFAULT_CLASS_CODE
    strict: bool = True
    include_goods: bool = False


# Categories used in the rejection summary. Keep in sync with the README.
REJECT_CATEGORIES = (
    "classCode_not_allowed",
    "weak_gold_match",
    "goods_excluded",
    "missing_ticker",
    "missing_classCode",
)


def _first_text(*values: Any) -> str | None:
    for v in values:
        if isinstance(v, str) and v.strip():
            return v
    return None


def _name_matches_gold(*texts: Any) -> str | None:
    for text in texts:
        if not isinstance(text, str) or not text.strip():
            continue
        for pat in GOLD_NAME_PATTERNS:
            m = pat.search(text)
            if m:
                return m.group(0)
    return None


def _strong_ticker_match(ticker: str) -> str | None:
    """Return a short reason if the ticker is a strong gold match, else None."""
    if STRICT_GD_TICKER_RE.match(ticker):
        return "gd_month_code"
    for token in STRICT_TICKER_TOKENS:
        if token in ticker:
            return f"ticker_contains_{token}"
    return None


def evaluate_candidate(
    instrument: dict[str, Any],
    raw: dict[str, Any] | None,
    cfg: FilterConfig,
) -> tuple[bool, str]:
    """Decide whether an instrument is a gold-futures candidate.

    Returns ``(accepted, reason)`` where ``reason`` is one of the accept tags
    (e.g. ``gd_month_code``, ``name_matches_gold``) when accepted, or one of
    :data:`REJECT_CATEGORIES` when rejected.
    """
    ticker = (instrument.get("ticker") or "").strip().upper()
    class_code = (instrument.get("classCode") or "").strip().upper()

    if not ticker:
        return False, "missing_ticker"
    if not class_code:
        return False, "missing_classCode"

    inst_type = (
        instrument.get("instrumentType") or instrument.get("type") or ""
    ).strip().upper()
    if inst_type == "GOODS" and not cfg.include_goods:
        return False, "goods_excluded"

    if cfg.moex_only and cfg.class_code and class_code != cfg.class_code.upper():
        return False, "classCode_not_allowed"

    # Strong ticker check.
    ticker_reason = _strong_ticker_match(ticker)
    if ticker_reason:
        return True, ticker_reason

    # Strong name check — also rescues AU-prefixed tickers that explicitly
    # name gold in their display/short name.
    raw_name = (raw or {}).get("name") if isinstance(raw, dict) else None
    name_match = _name_matches_gold(
        instrument.get("displayName"),
        instrument.get("shortName"),
        raw_name,
    )
    if name_match:
        return True, f"name_matches_{name_match.lower()}"

    # In relaxed mode (--no-strict), permit weaker ticker substrings only when
    # the instrument is on the MOEX futures classCode. We still never accept
    # bare ``AU`` substrings (that is the exact false-positive we are fixing).
    if not cfg.strict and class_code == cfg.class_code.upper():
        for token in ("GLDRUBF",):  # explicit relaxed tokens, easy to extend
            if token in ticker:
                return True, f"relaxed_ticker_contains_{token}"

    return False, "weak_gold_match"


# ---------------------------------------------------------------------------
# Candidate model
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


@dataclass
class RejectedRow:
    ticker: str | None
    classCode: str | None
    displayName: str | None
    shortName: str | None
    instrumentType: str | None
    source: str
    rejection_reason: str


def _pick_iso_date(raw: dict[str, Any] | None, keys: tuple[str, ...]) -> str | None:
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


def _to_candidate(
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
        maturityDate=_pick_iso_date(raw, ("maturityDate", "maturity")),
        expirationDate=_pick_iso_date(raw, ("expirationDate", "expireDate", "expiry")),
        settlementDate=_pick_iso_date(raw, ("settlementDate", "settlement")),
        tradingCurrency=instrument.get("tradingCurrency"),
        lotSize=instrument.get("lotSize"),
        minimumStep=instrument.get("minimumStep"),
        boards=instrument.get("boards") or [],
        source=source,
        match_reason=match_reason,
    )


def _to_rejected(
    instrument: dict[str, Any],
    *,
    source: str,
    reason: str,
) -> RejectedRow:
    return RejectedRow(
        ticker=instrument.get("ticker"),
        classCode=instrument.get("classCode"),
        displayName=instrument.get("displayName"),
        shortName=instrument.get("shortName"),
        instrumentType=instrument.get("instrumentType") or instrument.get("type"),
        source=source,
        rejection_reason=reason,
    )


# ---------------------------------------------------------------------------
# Discovery orchestration
# ---------------------------------------------------------------------------


@dataclass
class DiscoveryResult:
    accepted: list[Candidate]
    rejected: list[RejectedRow]
    rejection_summary: dict[str, int]
    sources: list[dict[str, Any]]
    total_raw: int
    filter_config: FilterConfig


def _classify(
    instruments: list[dict[str, Any]],
    *,
    source: str,
    cfg: FilterConfig,
    accepted: list[Candidate],
    rejected: list[RejectedRow],
    rejection_counter: Counter[str],
) -> None:
    for inst in instruments:
        raw = inst.get("raw") if isinstance(inst.get("raw"), dict) else None
        ok, reason = evaluate_candidate(inst, raw, cfg)
        if ok:
            cand = _to_candidate(inst, source=source, match_reason=reason)
            if cand is not None:
                accepted.append(cand)
            else:
                rejection_counter["missing_ticker"] += 1
                rejected.append(_to_rejected(inst, source=source, reason="missing_ticker"))
        else:
            rejection_counter[reason] += 1
            rejected.append(_to_rejected(inst, source=source, reason=reason))


def _dedupe_accepted(accepted: list[Candidate]) -> list[Candidate]:
    seen: set[tuple[str, str | None]] = set()
    out: list[Candidate] = []
    for c in accepted:
        key = (c.ticker.upper(), (c.classCode or "").upper() or None)
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def discover(
    cfg: FilterConfig,
    *,
    probe_base_asset: bool = True,
    page_size: int = 50,
    max_pages: int = 200,
    max_candidates: int = 0,
) -> DiscoveryResult:
    """Run discovery against BCS using the supplied filter configuration."""
    access_token = get_access_token()

    sources_used: list[dict[str, Any]] = []
    accepted: list[Candidate] = []
    rejected: list[RejectedRow] = []
    rejection_counter: Counter[str] = Counter({k: 0 for k in REJECT_CATEGORIES})
    total_raw = 0

    futures_result = fetch_instruments_by_type(
        access_token,
        instrument_type="FUTURES",
        limit=page_size,
        max_pages=max_pages,
        include_raw=True,
        page_size_param=BCS_INSTRUMENTS_BY_TYPE_SIZE_PARAM,
    )
    total_raw += len(futures_result.instruments)
    sources_used.append(
        {
            "endpoint": futures_result.endpoint_url,
            "params": {"type": "FUTURES", "size": page_size},
            "total_returned": len(futures_result.instruments),
            "pages": futures_result.pages,
            "truncated": futures_result.truncated,
        }
    )
    _classify(
        futures_result.instruments,
        source="by-type:FUTURES",
        cfg=cfg,
        accepted=accepted,
        rejected=rejected,
        rejection_counter=rejection_counter,
    )

    if cfg.include_goods:
        try:
            goods_result = fetch_instruments_by_type(
                access_token,
                instrument_type="GOODS",
                limit=page_size,
                max_pages=max_pages,
                include_raw=True,
                page_size_param=BCS_INSTRUMENTS_BY_TYPE_SIZE_PARAM,
            )
            total_raw += len(goods_result.instruments)
            sources_used.append(
                {
                    "endpoint": goods_result.endpoint_url,
                    "params": {"type": "GOODS", "size": page_size},
                    "total_returned": len(goods_result.instruments),
                    "pages": goods_result.pages,
                    "truncated": goods_result.truncated,
                }
            )
            _classify(
                goods_result.instruments,
                source="by-type:GOODS",
                cfg=cfg,
                accepted=accepted,
                rejected=rejected,
                rejection_counter=rejection_counter,
            )
        except BcsError as exc:
            sources_used.append(
                {
                    "endpoint": "by-type:GOODS",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

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
                total_raw += len(probe.instruments)
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
                _classify(
                    probe.instruments,
                    source=f"by-type:FUTURES?baseAssetTicker={base_ticker}",
                    cfg=cfg,
                    accepted=accepted,
                    rejected=rejected,
                    rejection_counter=rejection_counter,
                )
            except ValueError:
                continue
            except BcsError as exc:
                sources_used.append(
                    {
                        "endpoint": "by-type:FUTURES (base asset probe)",
                        "params": {"baseAssetTicker": base_ticker},
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )

    accepted = _dedupe_accepted(accepted)
    if max_candidates and max_candidates > 0:
        accepted = accepted[:max_candidates]

    return DiscoveryResult(
        accepted=accepted,
        rejected=rejected,
        rejection_summary=dict(rejection_counter),
        sources=sources_used,
        total_raw=total_raw,
        filter_config=cfg,
    )


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


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def write_outputs(result: DiscoveryResult) -> tuple[Path, Path, Path]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORT_DIR / "gold_futures_discovery.json"
    csv_path = REPORT_DIR / "gold_futures_discovery.csv"
    rejected_path = REPORT_DIR / "gold_futures_discovery_rejected.csv"

    payload = {
        "generatedAt": utc_now_iso(),
        "filter": {
            "moex_only": result.filter_config.moex_only,
            "class_code": result.filter_config.class_code,
            "strict": result.filter_config.strict,
            "include_goods": result.filter_config.include_goods,
        },
        "totalRaw": result.total_raw,
        "totalAccepted": len(result.accepted),
        "totalRejected": len(result.rejected),
        "rejectionSummary": result.rejection_summary,
        "sources": result.sources,
        "candidates": [_candidate_to_dict(c) for c in result.accepted],
    }
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    accepted_fieldnames = [
        "ticker", "classCode", "displayName", "shortName", "instrumentType",
        "baseAssetTicker", "maturityDate", "expirationDate", "settlementDate",
        "tradingCurrency", "lotSize", "minimumStep", "boards", "source",
        "match_reason",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=accepted_fieldnames)
        writer.writeheader()
        for c in result.accepted:
            row = _candidate_to_dict(c)
            row["boards"] = json.dumps(row["boards"], ensure_ascii=False)
            writer.writerow(row)

    rejected_fieldnames = [
        "ticker", "classCode", "displayName", "shortName", "instrumentType",
        "source", "rejection_reason",
    ]
    with rejected_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rejected_fieldnames)
        writer.writeheader()
        for r in result.rejected:
            writer.writerow(
                {
                    "ticker": r.ticker,
                    "classCode": r.classCode,
                    "displayName": r.displayName,
                    "shortName": r.shortName,
                    "instrumentType": r.instrumentType,
                    "source": r.source,
                    "rejection_reason": r.rejection_reason,
                }
            )

    return json_path, csv_path, rejected_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Discover gold futures candidates on BCS (strict by default).")
    p.add_argument(
        "--class-code",
        default=DEFAULT_CLASS_CODE,
        help=f"Required classCode when --moex-only is on (default: {DEFAULT_CLASS_CODE}).",
    )
    p.add_argument(
        "--moex-only",
        dest="moex_only",
        action="store_true",
        default=True,
        help="Only accept candidates whose classCode matches --class-code (default).",
    )
    p.add_argument(
        "--no-moex-only",
        dest="moex_only",
        action="store_false",
        help="Disable the classCode restriction.",
    )
    p.add_argument(
        "--strict",
        dest="strict",
        action="store_true",
        default=True,
        help="Strict gold filter — bare AU substring rejected (default).",
    )
    p.add_argument(
        "--no-strict",
        dest="strict",
        action="store_false",
        help="Relaxed filter — still rejects bare AU but allows softer tokens like GLDRUBF.",
    )
    p.add_argument(
        "--include-goods",
        action="store_true",
        help="Also accept GOODS-typed instruments (default: skip GOODS).",
    )
    p.add_argument(
        "--max-candidates",
        type=int,
        default=0,
        help="If > 0, cap accepted candidates to the first N after deduplication.",
    )
    p.add_argument(
        "--page-size",
        type=int,
        default=50,
        help="BCS page size (default: 50).",
    )
    p.add_argument(
        "--max-pages",
        type=int,
        default=200,
        help="Pagination safety cap (default: 200).",
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

    cfg = FilterConfig(
        moex_only=args.moex_only,
        class_code=args.class_code.upper(),
        strict=args.strict,
        include_goods=args.include_goods,
    )

    try:
        result = discover(
            cfg,
            probe_base_asset=not args.skip_base_asset_probe,
            page_size=args.page_size,
            max_pages=args.max_pages,
            max_candidates=args.max_candidates,
        )
    except BcsError as exc:
        print(f"BCS discovery failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3

    json_path, csv_path, rejected_path = write_outputs(result)

    print(
        "[discover-gold-futures] filter "
        f"strict={cfg.strict} moex_only={cfg.moex_only} "
        f"class_code={cfg.class_code} include_goods={cfg.include_goods}"
    )
    print(
        f"[discover-gold-futures] raw={result.total_raw} "
        f"accepted={len(result.accepted)} rejected={len(result.rejected)}"
    )
    if result.rejection_summary:
        non_zero = {k: v for k, v in result.rejection_summary.items() if v}
        if non_zero:
            print(f"[discover-gold-futures] rejection summary: {non_zero}")
    for c in result.accepted[:20]:
        print(
            f"  + {c.ticker:<14} class={c.classCode or '?':<10} "
            f"type={c.instrumentType or '?':<10} {c.match_reason}"
        )
    if len(result.accepted) > 20:
        print(f"  ... ({len(result.accepted) - 20} more)")
    print(f"[discover-gold-futures] wrote {json_path.relative_to(_REPO_ROOT)}")
    print(f"[discover-gold-futures] wrote {csv_path.relative_to(_REPO_ROOT)}")
    print(f"[discover-gold-futures] wrote {rejected_path.relative_to(_REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
