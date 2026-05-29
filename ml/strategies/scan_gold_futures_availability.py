"""
Scan candle availability for discovered gold futures candidates.

For every candidate in ``ml/reports/strategies/gold_futures_discovery.json``
this script probes the BCS candles endpoint over a small grid of timeframes
and date ranges, and records how many bars came back. It does NOT save raw
candle CSV — that is the job of ``download_bcs_contract_history.py``.

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

# Allow running as a script.
_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ml.strategies.download_bcs_goods_history import (  # noqa: E402
    BcsAuthError,
    BcsDownloadError,
    BcsHttpError,
    DEFAULT_CLIENT_ID,
    exchange_refresh_token,
    fetch_candles_window,
)


DISCOVERY_PATH = _REPO_ROOT / "ml" / "reports" / "strategies" / "gold_futures_discovery.json"
REPORT_DIR = _REPO_ROOT / "ml" / "reports" / "strategies"

DEFAULT_TIMEFRAMES = ("M5", "M15", "H1", "H4", "D")
INTER_REQUEST_DELAY = 0.4  # seconds; matches the polite delay used elsewhere.


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


def _load_candidates(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(
            f"Discovery file not found at {path}. "
            "Run `python ml/strategies/discover_gold_futures.py` first."
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError(f"{path} does not contain a 'candidates' list.")
    return [c for c in candidates if isinstance(c, dict) and c.get("ticker")]


def _allowed_timeframes_for_range(range_name: str, timeframes: tuple[str, ...]) -> list[str]:
    """Filter out impractical timeframe/range combos.

    A 7-day window with daily candles is fine; a multi-year window with M5 is
    not — it would take hundreds of HTTP calls per ticker. Keep intraday
    ranges short and broad ranges coarse.
    """
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
                return rows  # token broken; stop trying.
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
            except Exception as exc:  # defensive: never crash the whole scan.
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


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Probe BCS candle availability for discovered gold futures.")
    p.add_argument(
        "--input",
        default=str(DISCOVERY_PATH),
        help="Discovery JSON produced by discover_gold_futures.py.",
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
        help="If > 0, only probe the first N candidates (useful for smoke tests).",
    )
    p.add_argument(
        "--delay",
        type=float,
        default=INTER_REQUEST_DELAY,
        help=f"Sleep between requests in seconds (default: {INTER_REQUEST_DELAY}).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_argparser().parse_args(argv)
    refresh_token = os.getenv("BCS_REFRESH_TOKEN", "").strip()
    if not refresh_token:
        print("BCS_REFRESH_TOKEN env var is not set.", file=sys.stderr)
        return 2
    client_id = os.getenv("BCS_CLIENT_ID", DEFAULT_CLIENT_ID).strip() or DEFAULT_CLIENT_ID

    try:
        candidates = _load_candidates(Path(args.input))
    except (FileNotFoundError, ValueError) as exc:
        print(f"[scan-gold-futures] cannot load discovery: {exc}", file=sys.stderr)
        return 4

    if args.max_candidates and args.max_candidates > 0:
        candidates = candidates[: args.max_candidates]

    try:
        access_token = exchange_refresh_token(refresh_token, client_id=client_id)
    except BcsDownloadError as exc:
        print(f"BCS auth failed: {type(exc).__name__}", file=sys.stderr)
        return 3

    ranges = default_ranges(from_2024=not args.skip_from_2024)
    timeframes = tuple(tf.upper() for tf in args.timeframes)

    print(f"[scan-gold-futures] probing {len(candidates)} candidate(s) "
          f"x {len(timeframes)} timeframe(s) x {len(ranges)} range(s)")
    all_rows: list[AvailabilityRow] = []
    for i, cand in enumerate(candidates, 1):
        ticker = cand.get("ticker")
        class_code = cand.get("classCode")
        if not ticker or not class_code:
            print(f"  ({i}/{len(candidates)}) skip — missing ticker/classCode")
            continue
        print(f"  ({i}/{len(candidates)}) {ticker}/{class_code}")
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
