"""
Download historical BCS GOODS candles via the BCS trade-api-market-data-connector.

This is research tooling: it pulls candles for one or more BCS GOODS instruments
(e.g. GOLD/FEG, SILVER/FEG, BRENT0826/FEG) across requested timeframes and saves
raw CSVs locally for offline backtesting.

Auth
----
Reads credentials from environment:

* BCS_REFRESH_TOKEN     refresh token issued by BCS (read-only required)
* BCS_CLIENT_ID         client id, defaults to "trade-api-read"

Tokens are NEVER printed or written to disk.

Endpoints
---------
Token   : POST https://be.broker.ru/trade-api-keycloak/realms/tradeapi
                /protocol/openid-connect/token
Candles : GET  https://be.broker.ru/trade-api-market-data-connector
                /api/v1/candles-chart

Output
------
ml/data/raw_bcs/<TICKER>_<CLASSCODE>_<TIMEFRAME>.csv

Columns: timestamp, open, high, low, close, volume, ticker, classCode, timeframe.
The ml/data/ tree is gitignored, so these files stay local.

Usage
-----
    python ml/strategies/download_bcs_goods_history.py \\
        --ticker GOLD --class-code FEG --timeframes M5 M15 H1

Defaults: GOLD/FEG, M5+M15+H1, 2024-01-01 -> today.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import requests


REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = REPO_ROOT / "ml" / "data" / "raw_bcs"

BCS_BASE_URL = os.getenv("BCS_BASE_URL", "https://be.broker.ru").rstrip("/")
BCS_TOKEN_URL = os.getenv(
    "BCS_TOKEN_URL",
    f"{BCS_BASE_URL}/trade-api-keycloak/realms/tradeapi/protocol/openid-connect/token",
)
BCS_CANDLES_URL = os.getenv(
    "BCS_CANDLES_URL",
    f"{BCS_BASE_URL}/trade-api-market-data-connector/api/v1/candles-chart",
)
DEFAULT_CLIENT_ID = "trade-api-read"

REQUEST_TIMEOUT_SECONDS = 30
INTER_REQUEST_DELAY = 0.4  # Be polite to BCS.
MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 2.0

# Chunk sizes (days) per timeframe — small enough to stay under BCS limits.
CHUNK_DAYS: dict[str, int] = {
    "M5": 5,
    "M15": 10,
    "H1": 60,
    "H4": 180,
    "D": 1500,
}

SUPPORTED_TIMEFRAMES = list(CHUNK_DAYS.keys())


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class BcsDownloadError(RuntimeError):
    """Base class for downloader errors."""


class BcsAuthError(BcsDownloadError):
    """Refresh-token exchange failed."""


class BcsHttpError(BcsDownloadError):
    """Unexpected BCS HTTP response."""


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def exchange_refresh_token(
    refresh_token: str,
    client_id: str = DEFAULT_CLIENT_ID,
    timeout: int = REQUEST_TIMEOUT_SECONDS,
) -> str:
    """Exchange the BCS refresh token for an access token. Never logged."""
    if not refresh_token:
        raise BcsAuthError(
            "BCS_REFRESH_TOKEN is empty. Set it in the environment before downloading."
        )
    try:
        resp = requests.post(
            BCS_TOKEN_URL,
            data={
                "client_id": client_id or DEFAULT_CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise BcsAuthError(f"Network error exchanging BCS refresh token: {type(exc).__name__}") from exc

    if resp.status_code == 429:
        raise BcsHttpError("BCS auth rate-limited (HTTP 429). Retry later.")
    if not resp.ok:
        raise BcsAuthError(f"BCS auth failed: HTTP {resp.status_code}")
    try:
        payload = resp.json()
    except ValueError as exc:
        raise BcsAuthError("BCS auth response was not valid JSON.") from exc

    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise BcsAuthError("BCS auth response did not contain access_token.")
    return access_token


# ---------------------------------------------------------------------------
# Candle fetch
# ---------------------------------------------------------------------------


@dataclass
class CandleRow:
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float


def _read_num(raw: dict, keys: Iterable[str]) -> float | None:
    for key in keys:
        v = raw.get(key)
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f != f or f in (float("inf"), float("-inf")):
            continue
        return f
    return None


def _read_iso_timestamp(raw: dict) -> str | None:
    """Return ISO-8601 UTC timestamp string, or None."""
    val = raw.get("time") or raw.get("t") or raw.get("date") or raw.get("begin") or raw.get("timestamp")
    if val is None:
        return None
    try:
        if isinstance(val, (int, float)):
            # Heuristic: ms vs s epoch.
            seconds = val / 1000.0 if val > 1_000_000_000_000 else val
            dt = datetime.fromtimestamp(seconds, tz=timezone.utc)
        else:
            text = str(val).strip()
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
    except (ValueError, OSError):
        return None
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def _ohlc_valid(o: float, h: float, l: float, c: float) -> bool:
    if any(v != v or v in (float("inf"), float("-inf")) for v in (o, h, l, c)):
        return False
    if h < l:
        return False
    if h < max(o, c) - 1e-9:
        return False
    if l > min(o, c) + 1e-9:
        return False
    if min(o, h, l, c) <= 0:
        return False
    return True


def fetch_candles_window(
    access_token: str,
    ticker: str,
    class_code: str,
    timeframe: str,
    start_utc: datetime,
    end_utc: datetime,
    timeout: int = REQUEST_TIMEOUT_SECONDS,
) -> list[CandleRow]:
    """Fetch one window of candles. Caller chunks larger ranges."""
    params = {
        "ticker": ticker,
        "classCode": class_code,
        "timeFrame": timeframe,
        "startDate": start_utc.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "endDate":   end_utc.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(BCS_CANDLES_URL, params=params, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            last_exc = exc
            time.sleep(BACKOFF_BASE_SECONDS * (2 ** attempt))
            continue

        if resp.status_code == 429:
            time.sleep(BACKOFF_BASE_SECONDS * (2 ** attempt))
            continue
        if resp.status_code in (401, 403):
            raise BcsAuthError(f"BCS candles auth rejected (HTTP {resp.status_code}).")
        if resp.status_code == 400:
            # Bad range or no data — skip the window quietly.
            return []
        if not resp.ok:
            raise BcsHttpError(f"BCS candles failed: HTTP {resp.status_code}")

        try:
            payload = resp.json()
        except ValueError as exc:
            raise BcsHttpError("BCS candles response was not valid JSON.") from exc

        bars = payload.get("bars") if isinstance(payload, dict) else None
        if not isinstance(bars, list):
            return []

        rows: list[CandleRow] = []
        for item in bars:
            if not isinstance(item, dict):
                continue
            ts = _read_iso_timestamp(item)
            if ts is None:
                continue
            o = _read_num(item, ("open", "o"))
            h = _read_num(item, ("high", "h"))
            l = _read_num(item, ("low", "l"))
            c = _read_num(item, ("close", "c"))
            v = _read_num(item, ("volume", "v", "vol"))
            if o is None or h is None or l is None or c is None:
                continue
            if not _ohlc_valid(o, h, l, c):
                continue
            rows.append(CandleRow(ts, o, h, l, c, v if v is not None else 0.0))
        return rows

    raise BcsHttpError(
        f"BCS candles failed after {MAX_RETRIES} attempts"
        + (f": {type(last_exc).__name__}" if last_exc else "")
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def parse_date(text: str) -> datetime:
    """Parse YYYY-MM-DD into a UTC midnight datetime."""
    dt = datetime.strptime(text, "%Y-%m-%d")
    return dt.replace(tzinfo=timezone.utc)


def iter_chunks(start: datetime, end: datetime, chunk_days: int) -> Iterable[tuple[datetime, datetime]]:
    """Yield (chunk_start, chunk_end) windows covering [start, end]."""
    cursor = start
    delta = timedelta(days=chunk_days)
    while cursor < end:
        chunk_end = min(cursor + delta, end)
        yield cursor, chunk_end
        cursor = chunk_end


def deduplicate_and_sort(rows: list[CandleRow]) -> list[CandleRow]:
    """Sort ascending by timestamp and drop duplicates by timestamp."""
    rows.sort(key=lambda r: r.timestamp)
    out: list[CandleRow] = []
    last_ts: str | None = None
    for r in rows:
        if r.timestamp == last_ts:
            continue
        out.append(r)
        last_ts = r.timestamp
    return out


def save_csv(path: Path, rows: list[CandleRow], ticker: str, class_code: str, timeframe: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume", "ticker", "classCode", "timeframe"])
        for r in rows:
            writer.writerow([r.timestamp, r.open, r.high, r.low, r.close, r.volume, ticker, class_code, timeframe])


def download_ticker_timeframe(
    access_token: str,
    ticker: str,
    class_code: str,
    timeframe: str,
    start: datetime,
    end: datetime,
) -> Path:
    chunk_days = CHUNK_DAYS.get(timeframe)
    if chunk_days is None:
        raise BcsDownloadError(f"Unsupported timeframe: {timeframe}")

    out_path = RAW_DIR / f"{ticker}_{class_code}_{timeframe}.csv"
    print(f"[bcs-goods] {ticker}/{class_code} {timeframe}: "
          f"{start.date()} -> {end.date()} (chunks of {chunk_days} d)")

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
        print(f"  chunk {i}/{len(chunks)} {chunk_start.date()}..{chunk_end.date()} -> {len(rows)} bars")
        time.sleep(INTER_REQUEST_DELAY)

    deduped = deduplicate_and_sort(collected)
    save_csv(out_path, deduped, ticker, class_code, timeframe)
    print(f"  saved {len(deduped)} bars -> {out_path.relative_to(REPO_ROOT)}")
    return out_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Download historical BCS GOODS candles for SuperTrend research.")
    p.add_argument("--ticker", default="GOLD", help="BCS instrument ticker (default: GOLD).")
    p.add_argument("--class-code", default="FEG", help="BCS classCode (default: FEG).")
    p.add_argument(
        "--timeframes",
        nargs="+",
        default=["M5", "M15", "H1"],
        choices=SUPPORTED_TIMEFRAMES,
        help="Timeframes to download (default: M5 M15 H1).",
    )
    p.add_argument("--from-date", default="2024-01-01", help="Start date YYYY-MM-DD (default: 2024-01-01).")
    p.add_argument("--to-date", default=None, help="End date YYYY-MM-DD (default: today UTC).")
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
        print(f"BCS auth failed: {exc}", file=sys.stderr)
        return 3

    start = parse_date(args.from_date)
    end = parse_date(args.to_date) if args.to_date else datetime.now(timezone.utc)

    failed = 0
    for tf in args.timeframes:
        try:
            download_ticker_timeframe(
                access_token=access_token,
                ticker=args.ticker.upper(),
                class_code=args.class_code.upper(),
                timeframe=tf,
                start=start,
                end=end,
            )
        except BcsDownloadError as exc:
            print(f"[bcs-goods] {tf} failed: {exc}", file=sys.stderr)
            failed += 1

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
