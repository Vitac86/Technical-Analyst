"""
Build the training dataset from raw MOEX 1-minute candles.

Pipeline per ticker:
  1. Load raw 1m CSV
  2. Aggregate to target timeframe (5m, 15m, etc.)
  3. Calculate features
  4. Calculate labels
  5. Drop NaN/inf rows
  6. Add metadata columns (ticker, timeframe, engine, market, board)

Final dataset is saved to ml/data/processed/dataset_<timeframe>.parquet.
Parquet keeps datetime types and is compact; use --csv flag to output CSV instead.

Usage (from repo root):
    python ml/build_dataset.py
    python ml/build_dataset.py --timeframe 15m
    python ml/build_dataset.py --csv
"""
import argparse
from pathlib import Path

import pandas as pd
import yaml

from features import FEATURE_COLUMNS, calculate_features, drop_invalid_feature_rows
from labels import (
    CLASS_NAMES, CLASS_TO_INT, LABEL_COL,
    create_labels_close, create_labels_tp_sl,
)

_ML_DIR = Path(__file__).parent
_REPO_ROOT = _ML_DIR.parent

_TF_MINUTES = {
    "1m": 1,
    "5m": 5,
    "10m": 10,
    "15m": 15,
    "30m": 30,
    "60m": 60,
    "1h": 60,
}


def load_config(config_path=None) -> dict:
    if config_path is None:
        config_path = _ML_DIR / "config" / "default.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def _rpath(rel: str) -> Path:
    p = Path(rel)
    return p if p.is_absolute() else (_REPO_ROOT / p).resolve()


def load_raw_candles(ticker: str, raw_dir: Path, interval: int = 1) -> pd.DataFrame:
    path = raw_dir / f"{ticker}_{interval}m.csv"
    if not path.exists():
        raise FileNotFoundError(f"Raw data not found: {path}  (run moex_download.py first)")
    df = pd.read_csv(path, parse_dates=["datetime"])
    df = (
        df.sort_values("datetime")
        .drop_duplicates("datetime")
        .reset_index(drop=True)
    )
    return df


def aggregate_to_tf(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Aggregate 1-minute OHLCV candles to a coarser timeframe."""
    if minutes == 1:
        return df
    df = df.set_index("datetime")
    agg = (
        df.resample(f"{minutes}min", closed="left", label="left")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    )
    # Drop empty buckets (non-trading periods)
    agg = agg.dropna(subset=["open", "close"])
    agg = agg[agg["volume"] > 0]
    return agg.reset_index()


def build_ticker_df(
    ticker: str,
    raw_dir: Path,
    config: dict,
    tf_minutes: int,
) -> pd.DataFrame:
    """Load, aggregate, featurise, and label one ticker."""
    df = load_raw_candles(ticker, raw_dir, interval=config["raw_download_interval"])
    print(f"  {ticker}: {len(df):,} raw 1m rows")

    tf = config["timeframe"]
    df = aggregate_to_tf(df, tf_minutes)
    print(f"  {ticker}: {len(df):,} rows after {tf} aggregation")

    df = calculate_features(df)

    lbl = config["label"]
    mode = lbl.get("mode", "close")
    store_future = lbl.get("store_future_returns", True)

    if mode == "close":
        df = create_labels_close(
            df,
            horizon_candles=lbl["horizon_candles"],
            up_threshold_pct=lbl.get("up_threshold_pct", 0.25),
            down_threshold_pct=lbl.get("down_threshold_pct", 0.25),
            store_future_returns=store_future,
        )
    elif mode == "tp_sl":
        df = create_labels_tp_sl(
            df,
            horizon_candles=lbl["horizon_candles"],
            take_profit_pct=lbl.get("take_profit_pct", 0.30),
            stop_loss_pct=lbl.get("stop_loss_pct", 0.20),
            flat_if_both_hit_same_candle=lbl.get("flat_if_both_hit_same_candle", True),
            store_future_returns=store_future,
        )
    else:
        raise ValueError(f"Unknown label mode '{mode}'. Use 'close' or 'tp_sl'.")

    df = drop_invalid_feature_rows(df)
    df = df.dropna(subset=[LABEL_COL]).reset_index(drop=True)
    df[LABEL_COL] = df[LABEL_COL].astype(int)

    # Metadata columns
    df["ticker"] = ticker
    df["timeframe"] = tf
    df["engine"] = config["engine"]
    df["market"] = config["market"]
    df["board"] = config["board"]

    return df


def build_dataset(config: dict) -> pd.DataFrame:
    raw_dir = _rpath(config["output"]["raw_dir"])
    tickers = config["tickers"]
    tf = config["timeframe"]

    if tf not in _TF_MINUTES:
        raise ValueError(f"Unsupported timeframe '{tf}'. Choose from: {list(_TF_MINUTES)}")
    tf_minutes = _TF_MINUTES[tf]

    print(f"Building dataset — {len(tickers)} tickers, timeframe={tf}\n")

    frames = []
    skipped = []
    for ticker in tickers:
        try:
            df = build_ticker_df(ticker, raw_dir, config, tf_minutes)
            print(f"  {ticker}: {len(df):,} labeled rows  OK")
            frames.append(df)
        except FileNotFoundError as exc:
            print(f"  [skip] {exc}")
            skipped.append(ticker)
        except Exception as exc:
            print(f"  [error] {ticker}: {exc}")
            skipped.append(ticker)

    if not frames:
        raise RuntimeError(
            "No ticker data was loaded. Run moex_download.py first to fetch raw candles."
        )

    full = pd.concat(frames, ignore_index=True)
    full = full.sort_values(["datetime", "ticker"]).reset_index(drop=True)

    print(f"\nTotal rows: {len(full):,}  ({len(frames)} tickers)")
    label_dist = (
        full[LABEL_COL].map(INT_TO_CLASS_MAP).value_counts()
        if frames else pd.Series()
    )
    print("Label distribution:")
    for cls in CLASS_NAMES:
        n = (full[LABEL_COL] == CLASS_TO_INT[cls]).sum()
        pct = n / len(full) * 100
        print(f"  {cls}: {n:,}  ({pct:.1f}%)")

    if skipped:
        print(f"\n[warn] skipped tickers: {skipped}")

    return full


# Lookup used in build_dataset
INT_TO_CLASS_MAP = {v: k for k, v in CLASS_TO_INT.items()}


def save_dataset(df: pd.DataFrame, config: dict, use_csv: bool = False) -> Path:
    out_dir = _rpath(config["output"]["processed_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    tf = config["timeframe"]

    if use_csv:
        out_path = out_dir / f"dataset_{tf}.csv"
        df.to_csv(out_path, index=False)
    else:
        out_path = out_dir / f"dataset_{tf}.parquet"
        df.to_parquet(out_path, index=False)

    print(f"\nDataset saved: {out_path}")
    return out_path


def main():
    parser = argparse.ArgumentParser(description="Build ML training dataset from raw MOEX candles")
    parser.add_argument("--config", default=None)
    parser.add_argument(
        "--timeframe", default=None,
        help="Override timeframe from config (e.g. 5m, 15m)"
    )
    parser.add_argument("--csv", action="store_true", help="Save as CSV instead of Parquet")
    args = parser.parse_args()

    config = load_config(args.config)
    if args.timeframe:
        config["timeframe"] = args.timeframe

    df = build_dataset(config)
    save_dataset(df, config, use_csv=args.csv)


if __name__ == "__main__":
    main()
