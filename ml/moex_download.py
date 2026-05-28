"""
Download historical 1-minute candles from MOEX ISS.

Saves raw CSVs to ml/data/raw/<TICKER>_1m.csv.
Always downloads interval=1 (1-minute) regardless of training timeframe;
aggregation to 5m/15m/etc. is done by build_dataset.py.

Usage (from repo root):
    python ml/moex_download.py
    python ml/moex_download.py --force          # re-download existing files
    python ml/moex_download.py --tickers SBER GAZP
"""
import argparse
import sys
import time
from pathlib import Path

import pandas as pd
import requests
import yaml

_ML_DIR = Path(__file__).parent
_REPO_ROOT = _ML_DIR.parent

MOEX_ISS_BASE = "https://iss.moex.com/iss"
PAGE_SIZE = 500       # MOEX ISS default page size for candles
REQUEST_DELAY = 0.35  # seconds between paginated requests — be polite to MOEX servers


def load_config(config_path: str = None) -> dict:
    if config_path is None:
        config_path = _ML_DIR / "config" / "default.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def _rpath(rel: str) -> Path:
    """Resolve a relative path against repo root."""
    p = Path(rel)
    return p if p.is_absolute() else (_REPO_ROOT / p).resolve()


def download_ticker(
    session: requests.Session,
    ticker: str,
    engine: str,
    market: str,
    board: str,
    interval: int,
    date_from: str,
    date_to: str,
    out_dir: Path,
    force: bool = False,
) -> Path:
    """
    Download all candles for one ticker via paginated MOEX ISS requests.

    Returns path to the saved CSV.
    """
    out_file = out_dir / f"{ticker}_{interval}m.csv"

    if out_file.exists() and not force:
        print(f"  [skip] {ticker}: already downloaded → {out_file.name}")
        return out_file

    url = (
        f"{MOEX_ISS_BASE}/engines/{engine}/markets/{market}"
        f"/boards/{board}/securities/{ticker}/candles.json"
    )

    pages = []
    start = 0
    page_num = 0

    while True:
        params = {
            "from": date_from,
            "till": date_to,
            "interval": interval,
            "start": start,
            "iss.meta": "off",
        }
        try:
            resp = session.get(url, params=params, timeout=30)
            resp.raise_for_status()
        except requests.exceptions.HTTPError as exc:
            print(f"  [error] {ticker} page {page_num}: HTTP {exc.response.status_code}")
            break
        except requests.exceptions.RequestException as exc:
            print(f"  [error] {ticker} page {page_num}: {exc}")
            break

        payload = resp.json()
        candles = payload.get("candles", {})
        columns = candles.get("columns", [])
        rows = candles.get("data", [])

        if not rows:
            break

        pages.append(pd.DataFrame(rows, columns=columns))
        total_so_far = sum(len(p) for p in pages)
        page_num += 1
        print(f"  {ticker}: page {page_num}, rows fetched: {total_so_far}")

        if len(rows) < PAGE_SIZE:
            # Last page — no more data
            break

        start += PAGE_SIZE
        time.sleep(REQUEST_DELAY)

    if not pages:
        print(f"  [warn] {ticker}: no data returned for {date_from} → {date_to}")
        return out_file

    df = pd.concat(pages, ignore_index=True)

    # Normalise to standard OHLCV columns
    df = df.rename(columns={"begin": "datetime"})
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df[["datetime", "open", "high", "low", "close", "volume"]]
    df = (
        df.sort_values("datetime")
        .drop_duplicates("datetime")
        .reset_index(drop=True)
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_file, index=False)
    print(f"  [done] {ticker}: {len(df):,} candles saved → {out_file}")
    return out_file


def main():
    parser = argparse.ArgumentParser(
        description="Download MOEX ISS candles for configured tickers"
    )
    parser.add_argument("--config", default=None, help="Path to config YAML")
    parser.add_argument(
        "--tickers", nargs="+", default=None,
        help="Override tickers from config (e.g. --tickers SBER GAZP)"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-download even if file already exists"
    )
    args = parser.parse_args()

    config = load_config(args.config)
    tickers = args.tickers or config["tickers"]
    engine = config["engine"]
    market = config["market"]
    board = config["board"]
    interval = config["raw_download_interval"]
    date_from = config["date_from"]
    date_to = config["date_to"]
    out_dir = _rpath(config["output"]["raw_dir"])

    print(
        f"Downloading {len(tickers)} ticker(s)\n"
        f"  board={board}  interval={interval}m  {date_from} → {date_to}\n"
        f"  output: {out_dir}\n"
    )

    session = requests.Session()
    session.headers.update({"User-Agent": "TechnicalAnalystResearch/1.0"})

    errors = []
    for ticker in tickers:
        print(f"\n→ {ticker}")
        try:
            download_ticker(
                session=session,
                ticker=ticker,
                engine=engine,
                market=market,
                board=board,
                interval=interval,
                date_from=date_from,
                date_to=date_to,
                out_dir=out_dir,
                force=args.force,
            )
        except Exception as exc:
            print(f"  [error] {ticker}: {exc}")
            errors.append(ticker)
        time.sleep(REQUEST_DELAY)

    print(f"\nFinished. {len(tickers) - len(errors)}/{len(tickers)} tickers OK.")
    if errors:
        print(f"Failed tickers: {errors}")
        sys.exit(1)


if __name__ == "__main__":
    main()
