"""
Scan candle availability for accepted gold futures candidates.

Inputs (in order of precedence)
-------------------------------
1. ``--tickers T1 T2 ...``  (with --class-code) — explicit manual override.
2. ``--input <CSV>`` (default ``ml/reports/strategies/gold_futures_discovery.csv``)
   — the accepted-only CSV produced by ``discover_gold_futures.py``.

By default only Moscow Exchange (BCS classCode ``SPBFUT``) gold-related
contracts are scanned. The strict gold filter from
``discover_gold_futures.py`` is re-applied on the loaded rows so a stale
CSV cannot reintroduce non-gold candidates.

Auth
----
Reads ``BCS_REFRESH_TOKEN`` from the environment. Tokens are not printed.

Endpoint
--------
GET https://be.broker.ru/trade-api-market-data-connector/api/v1/candles-chart

Output
------
ml/reports/strategies/gold_futures_availability.json
ml/reports/strategies/gold_futures_availability.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ml.strategies.discover_gold_futures import (  # noqa: E402
    DEFAULT_CLASS_CODE,
    FilterConfig,
    evaluate_candidate,
)
from ml.strategies.download_bcs_goods_history import (  # noqa: E402
    BcsAuthError,
    BcsDownloadError,
    BcsHttpError,
    DEFAULT_CLIENT_ID,
    exchange_refresh_token,
    fetch_candles_window,
)


REPORT_DIR = _REPO_ROOT / "ml" / "reports" / "strategies"
DISCOVERY_CSV_PATH = REPORT_DIR / "gold_futures_discovery.csv"

DEFAULT_TIMEFRAMES = ("M5", "M15", "H1", "H4", "D")
INTER_REQUEST_DELAY = 0.4
LARGE_CANDIDATE_WARNING_THRESHOLD = 50


@dataclass
class RangeSpec:
    name: str
    start: datetime
    end: datetime


@dataclass
class AvailabilityRow:
    ticker: str
    classCode: str
    timeframe: str
    range_name: str
    range_start: str
    range_end: str
    bars_count: int
    first_bar_time: str | None
    last_bar_time: str | None
    sample_close: float | None
    status: str
    error: str | None = None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def default_ranges(now: datetime | None = None, from_2024: bool = True) -> list[RangeSpec]:
    now = now or utc_now()
    ranges = [
        RangeSpec("last_7d", now - timedelta(days=7), now),
        RangeSpec("last_30d", now - timedelta(days=30), now),
        RangeSpec("last_90d", now - timedelta(days=90), now),
    ]
    if from_2024:
        ranges.append(
            RangeSpec(
                "from_2024_01_01",
                datetime(2024, 1, 1, tzinfo=timezone.utc),
                now,
            )
        )
    return ranges


# ---------------------------------------------------------------------------
# Loading candidates
# ---------------------------------------------------------------------------


def load_candidates_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(
            f"Discovery CSV not found at {path}. "
            "Run `python ml/strategies/discover_gold_futures.py` first."
        )
    candidates: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ticker = (row.get("ticker") or "").strip().upper()
            class_code = (row.get("classCode") or "").strip().upper()
            if not ticker or not class_code:
                continue
            candidates.append(
                {
                    "ticker": ticker,
                    "classCode": class_code,
                    "displayName": (row.get("displayName") or "").strip() or None,
                    "shortName": (row.get("shortName") or "").strip() or None,
                    "instrumentType": (row.get("instrumentType") or "").strip() or None,
                }
            )
    return candidates


def candidates_from_tickers(tickers: list[str], class_code: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    cc = (class_code or "").strip().upper()
    for raw_t in tickers:
        t = (raw_t or "").strip().upper()
        if not t or not cc:
            continue
        key = (t, cc)
        if key in seen:
            continue
        seen.add(key)
        out.append({"ticker": t, "classCode": cc})
    return out


# ---------------------------------------------------------------------------
# Range / timeframe limits
# ---------------------------------------------------------------------------


def _allowed_timeframes_for_range(range_name: str, timeframes: tuple[str, ...]) -> list[str]:
    if range_name in ("last_7d", "last_30d"):
        return list(timeframes)
    if range_name == "last_90d":
        return [tf for tf in timeframes if tf in {"M5", "M15", "H1", "H4", "D"}]
    if range_name == "from_2024_01_01":
        return [tf for tf in timeframes if tf in {"H1", "H4", "D"}]
    return list(timeframes)


def scan_candidate(
    access_token: str,
    candidate: dict[str, Any],
    ranges: list[RangeSpec],
    timeframes: tuple[str, ...],
    *,
    delay: float = INTER_REQUEST_DELAY,
) -> list[AvailabilityRow]:
    ticker = str(candidate.get("ticker") or "").upper()
    class_code = str(candidate.get("classCode") or "").upper()
    rows: list[AvailabilityRow] = []
    if not ticker or not class_code:
        return rows

    for range_spec in ranges:
        for timeframe in _allowed_timeframes_for_range(range_spec.name, timeframes):
            time.sleep(delay)
            row = AvailabilityRow(
                ticker=ticker,
                classCode=class_code,
                timeframe=timeframe,
                range_name=range_spec.name,
                range_start=range_spec.start.isoformat(timespec="seconds").replace("+00:00", "Z"),
                range_end=range_spec.end.isoformat(timespec="seconds").replace("+00:00", "Z"),
                bars_count=0,
                first_bar_time=None,
                last_bar_time=None,
                sample_close=None,
                status="empty",
            )
            try:
                bars = fetch_candles_window(
                    access_token=access_token,
                    ticker=ticker,
                    class_code=class_code,
                    timeframe=timeframe,
                    start_utc=range_spec.start,
                    end_utc=range_spec.end,
                )
            except BcsAuthError as exc:
                row.status = "auth_error"
                row.error = type(exc).__name__
                rows.append(row)
                return rows
            except BcsHttpError as exc:
                row.status = "http_error"
                row.error = type(exc).__name__
                rows.append(row)
                continue
            except BcsDownloadError as exc:
                row.status = "error"
                row.error = type(exc).__name__
                rows.append(row)
                continue
            except Exception as exc:  # defensive
                row.status = "error"
                row.error = type(exc).__name__
                rows.append(row)
                continue

            row.bars_count = len(bars)
            if bars:
                row.first_bar_time = bars[0].timestamp
                row.last_bar_time = bars[-1].timestamp
                row.sample_close = float(bars[-1].close)
                row.status = "ok"
            rows.append(row)
    return rows


def write_outputs(rows: list[AvailabilityRow]) -> tuple[Path, Path]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORT_DIR / "gold_futures_availability.json"
    csv_path = REPORT_DIR / "gold_futures_availability.csv"

    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "totalProbes": len(rows),
        "rows": [row.__dict__ for row in rows],
    }
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    if rows:
        fieldnames = list(rows[0].__dict__.keys())
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(row.__dict__)
    else:
        csv_path.write_text("", encoding="utf-8")
    return json_path, csv_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Probe BCS candle availability for accepted gold futures.")
    p.add_argument(
        "--input",
        default=str(DISCOVERY_CSV_PATH),
        help="Accepted-only discovery CSV from discover_gold_futures.py.",
    )
    p.add_argument(
        "--class-code",
        default=DEFAULT_CLASS_CODE,
        help=f"classCode used when --tickers is given or for strict re-filtering (default: {DEFAULT_CLASS_CODE}).",
    )
    p.add_argument(
        "--tickers",
        nargs="+",
        default=None,
        help="Explicit ticker list. Overrides --input; uses --class-code for each.",
    )
    p.add_argument(
        "--strict",
        dest="strict",
        action="store_true",
        default=True,
        help="Re-apply the strict gold filter on loaded candidates (default).",
    )
    p.add_argument(
        "--no-strict",
        dest="strict",
        action="store_false",
        help="Skip the strict re-filter when reading the CSV.",
    )
    p.add_argument(
        "--timeframes",
        nargs="+",
        default=list(DEFAULT_TIMEFRAMES),
        help=f"Timeframes to probe (default: {' '.join(DEFAULT_TIMEFRAMES)}).",
    )
    p.add_argument(
        "--skip-from-2024",
        action="store_true",
        help="Skip the wide 2024-01-01 to today probe.",
    )
    p.add_argument(
        "--max-candidates",
        type=int,
        default=0,
        help="If > 0, only probe the first N candidates.",
    )
    p.add_argument(
        "--delay",
        type=float,
        default=INTER_REQUEST_DELAY,
        help=f"Sleep between requests in seconds (default: {INTER_REQUEST_DELAY}).",
    )
    return p


def _apply_strict_filter(
    candidates: list[dict[str, Any]],
    cfg: FilterConfig,
) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], str]]]:
    accepted: list[dict[str, Any]] = []
    rejected: list[tuple[dict[str, Any], str]] = []
    for inst in candidates:
        ok, reason = evaluate_candidate(inst, raw=None, cfg=cfg)
        if ok:
            accepted.append(inst)
        else:
            rejected.append((inst, reason))
    return accepted, rejected


def main(argv: list[str] | None = None) -> int:
    args = build_argparser().parse_args(argv)
    refresh_token = os.getenv("BCS_REFRESH_TOKEN", "").strip()
    if not refresh_token:
        print("BCS_REFRESH_TOKEN env var is not set.", file=sys.stderr)
        return 2
    client_id = os.getenv("BCS_CLIENT_ID", DEFAULT_CLIENT_ID).strip() or DEFAULT_CLIENT_ID

    class_code = (args.class_code or DEFAULT_CLASS_CODE).strip().upper()

    if args.tickers:
        candidates = candidates_from_tickers(args.tickers, class_code)
        source_label = f"manual:{len(args.tickers)} tickers"
    else:
        try:
            candidates = load_candidates_csv(Path(args.input))
        except FileNotFoundError as exc:
            print(f"[scan-gold-futures] {exc}", file=sys.stderr)
            return 4
        source_label = f"csv:{Path(args.input).name}"

    cfg = FilterConfig(
        moex_only=True,
        class_code=class_code,
        strict=True,
        include_goods=False,
    )

    if args.strict and not args.tickers:
        accepted, rejected = _apply_strict_filter(candidates, cfg)
        if rejected:
            print(
                f"[scan-gold-futures] strict re-filter dropped {len(rejected)} "
                f"row(s) from {source_label}"
            )
            preview = rejected[:5]
            for inst, reason in preview:
                print(
                    f"  - {inst.get('ticker')}/{inst.get('classCode')} -> {reason}"
                )
            if len(rejected) > 5:
                print(f"  ... ({len(rejected) - 5} more dropped)")
        candidates = accepted

    if args.max_candidates and args.max_candidates > 0:
        candidates = candidates[: args.max_candidates]

    if len(candidates) > LARGE_CANDIDATE_WARNING_THRESHOLD:
        print(
            f"[scan-gold-futures] WARNING: {len(candidates)} candidates loaded "
            f"(> {LARGE_CANDIDATE_WARNING_THRESHOLD}). "
            "Too many gold candidates; discovery filter may be too broad."
        )

    if not candidates:
        print("[scan-gold-futures] no candidates to scan.", file=sys.stderr)
        return 5

    try:
        access_token = exchange_refresh_token(refresh_token, client_id=client_id)
    except BcsDownloadError as exc:
        print(f"BCS auth failed: {type(exc).__name__}", file=sys.stderr)
        return 3

    ranges = default_ranges(from_2024=not args.skip_from_2024)
    timeframes = tuple(tf.upper() for tf in args.timeframes)

    print(
        f"[scan-gold-futures] source={source_label} "
        f"class_code={class_code} strict={args.strict} "
        f"probing {len(candidates)} candidate(s) x {len(timeframes)} timeframe(s) "
        f"x {len(ranges)} range(s)"
    )
    all_rows: list[AvailabilityRow] = []
    for i, cand in enumerate(candidates, 1):
        ticker = cand.get("ticker")
        cand_class = cand.get("classCode")
        print(f"  ({i}/{len(candidates)}) {ticker}/{cand_class}")
        rows = scan_candidate(
            access_token,
            cand,
            ranges,
            timeframes,
            delay=args.delay,
        )
        ok_rows = [r for r in rows if r.status == "ok" and r.bars_count > 0]
        if ok_rows:
            print(
                f"     -> {len(ok_rows)} non-empty probes "
                f"(best: {max(r.bars_count for r in ok_rows)} bars)"
            )
        else:
            print("     -> no candle data in any tested window")
        all_rows.extend(rows)

    json_path, csv_path = write_outputs(all_rows)
    print(f"[scan-gold-futures] wrote {json_path.relative_to(_REPO_ROOT)}")
    print(f"[scan-gold-futures] wrote {csv_path.relative_to(_REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
