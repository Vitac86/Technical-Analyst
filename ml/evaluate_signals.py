"""
Signal threshold analysis for trained CatBoost direction models.

Evaluates predicted probabilities rather than hard class predictions.
For each probability threshold computes LONG/SHORT signal counts,
precision, coverage, and average future return (if available in dataset).

Signal rules (applied per row):
  LONG  if P(UP)   >= threshold AND P(UP)   > P(DOWN)
  SHORT if P(DOWN) >= threshold AND P(DOWN) > P(UP)
  NO_TRADE otherwise

Usage (from repo root):
    python ml/evaluate_signals.py
    python ml/evaluate_signals.py --model catboost_direction_v2_balanced
    python ml/evaluate_signals.py --model catboost_direction_v1_baseline --split val
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from catboost import CatBoostClassifier

from features import FEATURE_COLUMNS
from labels import CLASS_NAMES, CLASS_TO_INT, LABEL_COL

_ML_DIR = Path(__file__).parent
_REPO_ROOT = _ML_DIR.parent

FUTURE_CLOSE_COL = "future_close_return_pct"
FUTURE_MAX_COL   = "future_max_return_pct"
FUTURE_MIN_COL   = "future_min_return_pct"

DEFAULT_THRESHOLDS = [0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75]


def load_config(config_path=None) -> dict:
    if config_path is None:
        config_path = _ML_DIR / "config" / "default.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def _rpath(rel: str) -> Path:
    p = Path(rel)
    return p if p.is_absolute() else (_REPO_ROOT / p).resolve()


def load_dataset(config: dict) -> pd.DataFrame:
    tf = config["timeframe"]
    parquet_path = _rpath(config["output"]["processed_dir"]) / f"dataset_{tf}.parquet"
    csv_path = parquet_path.with_suffix(".csv")

    if parquet_path.exists():
        df = pd.read_parquet(parquet_path)
    elif csv_path.exists():
        df = pd.read_csv(csv_path, parse_dates=["datetime"])
    else:
        raise FileNotFoundError(
            f"Dataset not found: {parquet_path}\nRun build_dataset.py first."
        )
    return df.sort_values("datetime").reset_index(drop=True)


def time_split(df: pd.DataFrame, train_frac: float, val_frac: float):
    n = len(df)
    i_train = int(n * train_frac)
    i_val = int(n * (train_frac + val_frac))
    return df.iloc[:i_train].copy(), df.iloc[i_train:i_val].copy(), df.iloc[i_val:].copy()


def _safe_mean(arr):
    return round(float(np.mean(arr)), 4) if len(arr) > 0 else None


def analyze_threshold(
    df_split: pd.DataFrame,
    probas: np.ndarray,
    threshold: float,
) -> dict:
    """Compute signal metrics for a single probability threshold."""
    p_up   = probas[:, CLASS_TO_INT["UP"]]
    p_down = probas[:, CLASS_TO_INT["DOWN"]]
    y = df_split[LABEL_COL].values

    long_mask  = (p_up   >= threshold) & (p_up   > p_down)
    short_mask = (p_down >= threshold) & (p_down > p_up)
    no_trade_mask = ~(long_mask | short_mask)

    n_long     = int(long_mask.sum())
    n_short    = int(short_mask.sum())
    n_no_trade = int(no_trade_mask.sum())
    n_total    = len(df_split)
    coverage   = round((n_long + n_short) / n_total * 100, 2) if n_total > 0 else 0.0

    # Precision: LONG correct if actual label == UP; SHORT correct if actual == DOWN
    long_correct  = int((long_mask  & (y == CLASS_TO_INT["UP"])).sum())
    short_correct = int((short_mask & (y == CLASS_TO_INT["DOWN"])).sum())
    long_prec  = round(long_correct  / n_long,  4) if n_long  > 0 else None
    short_prec = round(short_correct / n_short, 4) if n_short > 0 else None

    combined_correct = long_correct + short_correct
    combined_signals = n_long + n_short
    combined_acc = round(combined_correct / combined_signals, 4) if combined_signals > 0 else None

    result = {
        "threshold": threshold,
        "long_signals": n_long,
        "short_signals": n_short,
        "no_trade": n_no_trade,
        "coverage_pct": coverage,
        "long_precision": long_prec,
        "short_precision": short_prec,
        "combined_signal_accuracy": combined_acc,
    }

    # Future return stats if columns available
    for col, key in [
        (FUTURE_CLOSE_COL, "avg_close_return"),
        (FUTURE_MAX_COL,   "avg_max_return"),
        (FUTURE_MIN_COL,   "avg_min_return"),
    ]:
        if col in df_split.columns:
            vals = df_split[col].values
            result[f"long_{key}"]  = _safe_mean(vals[long_mask])
            result[f"short_{key}"] = _safe_mean(-vals[short_mask])  # short profits from drops

    return result


def analyze_per_ticker(
    df_split: pd.DataFrame,
    probas: np.ndarray,
    thresholds: list,
) -> dict:
    """Per-ticker breakdown of signal counts and precision at each threshold."""
    if "ticker" not in df_split.columns:
        return {}

    tickers = sorted(df_split["ticker"].unique())
    results = {}
    y = df_split[LABEL_COL].values
    p_up   = probas[:, CLASS_TO_INT["UP"]]
    p_down = probas[:, CLASS_TO_INT["DOWN"]]

    for ticker in tickers:
        mask = (df_split["ticker"] == ticker).values
        if mask.sum() < 5:
            continue
        ticker_data: dict = {"rows": int(mask.sum()), "by_threshold": {}}
        for thr in thresholds:
            long_mask  = mask & (p_up   >= thr) & (p_up   > p_down)
            short_mask = mask & (p_down >= thr) & (p_down > p_up)
            n_long  = int(long_mask.sum())
            n_short = int(short_mask.sum())
            lp = round(int((long_mask  & (y == CLASS_TO_INT["UP"])).sum())   / n_long,  4) if n_long  > 0 else None
            sp = round(int((short_mask & (y == CLASS_TO_INT["DOWN"])).sum()) / n_short, 4) if n_short > 0 else None
            ticker_data["by_threshold"][str(thr)] = {
                "long_signals": n_long,
                "short_signals": n_short,
                "long_precision": lp,
                "short_precision": sp,
            }
        results[ticker] = ticker_data
    return results


def evaluate_model_signals(
    model: CatBoostClassifier,
    df_split: pd.DataFrame,
    split_name: str,
    thresholds: list,
) -> dict:
    X = df_split[FEATURE_COLUMNS].values
    probas = model.predict_proba(X)

    threshold_results = [analyze_threshold(df_split, probas, thr) for thr in thresholds]
    per_ticker = analyze_per_ticker(df_split, probas, thresholds)

    print(f"\n--- SIGNAL ANALYSIS: {split_name.upper()} ---")
    print(f"{'Thr':>5}  {'LONG':>6}  {'SHORT':>6}  {'NO_TRD':>7}  {'COV%':>5}  {'L_PREC':>7}  {'S_PREC':>7}")
    for r in threshold_results:
        print(
            f"{r['threshold']:>5.2f}  "
            f"{r['long_signals']:>6}  "
            f"{r['short_signals']:>6}  "
            f"{r['no_trade']:>7}  "
            f"{r['coverage_pct']:>5.1f}  "
            f"{str(r['long_precision']):>7}  "
            f"{str(r['short_precision']):>7}"
        )

    return {
        "split": split_name,
        "rows": len(df_split),
        "threshold_analysis": threshold_results,
        "per_ticker": per_ticker,
    }


def main():
    parser = argparse.ArgumentParser(description="Signal threshold analysis for direction models")
    parser.add_argument("--config", default=None)
    parser.add_argument("--timeframe", default=None)
    parser.add_argument(
        "--model",
        default="catboost_direction_v2_balanced",
        help="Model version name (without .cbm extension)",
    )
    parser.add_argument(
        "--split",
        default="test",
        choices=["val", "test", "both"],
        help="Which split to evaluate (default: test)",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    if args.timeframe:
        config["timeframe"] = args.timeframe

    thresholds = config.get("signal_thresholds", DEFAULT_THRESHOLDS)

    models_dir = _rpath(config["output"]["models_dir"])
    model_path = models_dir / f"{args.model}.cbm"
    if not model_path.exists():
        raise FileNotFoundError(
            f"Model not found: {model_path}\nRun train_catboost.py first."
        )

    model = CatBoostClassifier()
    model.load_model(str(model_path))
    print(f"Loaded model: {model_path}")

    df = load_dataset(config)
    train_cfg = config["train"]
    _, val, test = time_split(df, train_cfg["train_frac"], train_cfg["val_frac"])

    splits_to_run = []
    if args.split in ("val", "both"):
        splits_to_run.append(("val", val))
    if args.split in ("test", "both"):
        splits_to_run.append(("test", test))

    report: dict = {"model": args.model, "thresholds_evaluated": thresholds, "splits": {}}
    for split_name, df_split in splits_to_run:
        result = evaluate_model_signals(model, df_split, split_name, thresholds)
        report["splits"][split_name] = result

    reports_dir = _rpath(config["output"]["reports_dir"])
    reports_dir.mkdir(parents=True, exist_ok=True)
    out_path = reports_dir / f"{args.model}_signals.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nReport saved: {out_path}")


if __name__ == "__main__":
    main()
