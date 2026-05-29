"""
Download historical BCS candles for any contract (GOODS or FUTURES).

This is a generalisation of ``download_bcs_goods_history.py``: it works for
any ``ticker`` + ``classCode`` combination on the BCS candles endpoint, and
adds quality-of-life flags for repeated runs over many futures contracts:

* ``--skip-existing``    keep already-downloaded CSVs untouched
* ``--overwrite-empty``  re-fetch a CSV that exists but only has the header
                          (or zero rows)
* a final per-timeframe summary with bars / first ts / last ts

Auth and endpoints are shared with ``download_bcs_goods_history.py``.

Outputs
-------
ml/data/raw_bcs/<TICKER>_<CLASSCODE>_<TIMEFRAME>.csv  (gitignored)
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Allow running as a script from the repo root.
_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ml.strategies.download_bcs_goods_history import (  # noqa: E402
    BcsDownloadError,
    CHUNK_DAYS,
    CandleRow,
    DEFAULT_CLIENT_ID,
    INTER_REQUEST_DELAY,
    RAW_DIR,
    SUPPORTED_TIMEFRAMES,
    deduplicate_and_sort,
    exchange_refresh_token,
    fetch_candles_window,
    iter_chunks,
    parse_date,
    save_csv,
)


def _existing_bar_count(path: Path) -> int:
    """Return number of data rows in an existing CSV (-1 if file missing)."""
    if not path.exists():
        return -1
    try:
        with path.open("r", encoding="utf-8") as f:
            line_count = sum(1 for _ in f)
    except OSError:
        return 0
    # Subtract the header row.
    return max(line_count - 1, 0)


def download_one(
    *,
    access_token: str,
    ticker: str,
    class_code: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    skip_existing: bool,
    overwrite_empty: bool,
) -> dict[str, object]:
    chunk_days = CHUNK_DAYS.get(timeframe)
    if chunk_days is None:
        raise BcsDownloadError(f"Unsupported timeframe: {timeframe}")

    out_path = RAW_DIR / f"{ticker}_{class_code}_{timeframe}.csv"
    existing_bars = _existing_bar_count(out_path)
    if existing_bars > 0 and skip_existing:
        print(
            f"[bcs-contract] {ticker}/{class_code} {timeframe}: "
            f"skip-existing ({existing_bars} bars already on disk)"
        )
        return {
            "ticker": ticker,
            "classCode": class_code,
            "timeframe": timeframe,
            "status": "skipped_existing",
            "bars": existing_bars,
            "path": str(out_path.relative_to(_REPO_ROOT)),
        }
    if existing_bars == 0 and not overwrite_empty and out_path.exists():
        # Don't overwrite an empty file unless explicitly allowed.
        print(
            f"[bcs-contract] {ticker}/{class_code} {timeframe}: "
            f"existing CSV is empty (pass --overwrite-empty to re-fetch)"
        )
        return {
            "ticker": ticker,
            "classCode": class_code,
            "timeframe": timeframe,
            "status": "skipped_empty",
            "bars": 0,
            "path": str(out_path.relative_to(_REPO_ROOT)),
        }

    print(
        f"[bcs-contract] {ticker}/{class_code} {timeframe}: "
        f"{start.date()} -> {end.date()} (chunks of {chunk_days} d)"
    )
    collected: list[CandleRow] = []
    chunks = list(iter_chunks(start, end, chunk_days))
    for i, (chunk_start, chunk_end) in enumerate(chunks, 1):
        rows = fetch_candles_window(
            access_token=access_token,
            ticker=ticker,
            class_code=class_code,
            timeframe=timeframe,
            start_utc=chunk_start,
            end_utc=chunk_end,
        )
        collected.extend(rows)
        print(
            f"  chunk {i}/{len(chunks)} "
            f"{chunk_start.date()}..{chunk_end.date()} -> {len(rows)} bars"
        )
        time.sleep(INTER_REQUEST_DELAY)

    deduped = deduplicate_and_sort(collected)
    if not deduped and existing_bars > 0:
        print(
            f"  [bcs-contract] WARN: fetch returned 0 bars but existing CSV has "
            f"{existing_bars} bars — keeping existing file."
        )
        return {
            "ticker": ticker,
            "classCode": class_code,
            "timeframe": timeframe,
            "status": "kept_existing_nonempty",
            "bars": existing_bars,
            "path": str(out_path.relative_to(_REPO_ROOT)),
        }
    save_csv(out_path, deduped, ticker, class_code, timeframe)
    first_ts = deduped[0].timestamp if deduped else None
    last_ts = deduped[-1].timestamp if deduped else None
    print(f"  saved {len(deduped)} bars -> {out_path.relative_to(_REPO_ROOT)}")
    return {
        "ticker": ticker,
        "classCode": class_code,
        "timeframe": timeframe,
        "status": "ok" if deduped else "empty",
        "bars": len(deduped),
        "first_bar_time": first_ts,
        "last_bar_time": last_ts,
        "path": str(out_path.relative_to(_REPO_ROOT)),
    }


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Download historical BCS candles for any contract (GOODS or FUTURES)."
    )
    p.add_argument("--ticker", required=True, help="BCS instrument ticker (e.g. GDA-3.26).")
    p.add_argument("--class-code", required=True, help="BCS classCode (e.g. SPBFUT or FUT).")
    p.add_argument(
        "--timeframes",
        nargs="+",
        default=["M5", "M15", "H1"],
        choices=SUPPORTED_TIMEFRAMES,
        help="Timeframes to download (default: M5 M15 H1).",
    )
    p.add_argument("--from-date", default="2024-01-01", help="Start date YYYY-MM-DD.")
    p.add_argument("--to-date", default=None, help="End date YYYY-MM-DD (default: today UTC).")
    p.add_argument(
        "--skip-existing",
        action="store_true",
        help="Do not re-download timeframes that already have non-empty CSVs.",
    )
    p.add_argument(
        "--overwrite-empty",
        action="store_true",
        help="Re-fetch a CSV that exists but has zero data rows.",
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
        access_token = exchange_refresh_token(refresh_token, client_id=client_id)
    except BcsDownloadError as exc:
        print(f"BCS auth failed: {type(exc).__name__}", file=sys.stderr)
        return 3

    start = parse_date(args.from_date)
    end = parse_date(args.to_date) if args.to_date else datetime.now(timezone.utc)

    ticker = args.ticker.upper()
    class_code = args.class_code.upper()

    summaries: list[dict[str, object]] = []
    failed = 0
    for tf in args.timeframes:
        try:
            summary = download_one(
                access_token=access_token,
                ticker=ticker,
                class_code=class_code,
                timeframe=tf,
                start=start,
                end=end,
                skip_existing=args.skip_existing,
                overwrite_empty=args.overwrite_empty,
            )
            summaries.append(summary)
        except BcsDownloadError as exc:
            print(f"[bcs-contract] {tf} failed: {type(exc).__name__}", file=sys.stderr)
            failed += 1
            summaries.append(
                {
                    "ticker": ticker,
                    "classCode": class_code,
                    "timeframe": tf,
                    "status": "error",
                    "bars": 0,
                    "error": type(exc).__name__,
                }
            )

    print("[bcs-contract] summary:")
    for s in summaries:
        tf = s.get("timeframe")
        status = s.get("status")
        bars = s.get("bars")
        first_ts = s.get("first_bar_time")
        last_ts = s.get("last_bar_time")
        msg = f"  {tf}: {status} bars={bars}"
        if first_ts and last_ts:
            msg += f" range={first_ts}..{last_ts}"
        print(msg)

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
