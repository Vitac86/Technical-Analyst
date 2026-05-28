"""
Walk-forward validation for the direction model.

Uses an expanding window: each fold trains on all data before the validation
window and evaluates on the next fixed-size chunk.

Reports per-fold accuracy, class distribution, and signal statistics
(rows where predicted probability exceeds a configurable threshold).

Usage (from repo root):
    python ml/validate_walk_forward.py
    python ml/validate_walk_forward.py --folds 6 --threshold 0.60
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from catboost import CatBoostClassifier, Pool
from sklearn.metrics import accuracy_score, classification_report

from features import FEATURE_COLUMNS
from labels import CLASS_NAMES, CLASS_TO_INT, LABEL_COL

_ML_DIR = Path(__file__).parent
_REPO_ROOT = _ML_DIR.parent


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


def walk_forward_validate(
    df: pd.DataFrame,
    config: dict,
    n_folds: int = 5,
    signal_threshold: float = 0.55,
) -> dict:
    """
    Expanding-window walk-forward validation.

    The dataset is split into (n_folds + 1) equal chunks.
    Fold k trains on chunks 0..k and validates on chunk k+1.

    Args:
        df:               Dataset sorted by datetime.
        config:           Training config dict.
        n_folds:          Number of validation folds.
        signal_threshold: Min predicted probability to count as a signal.

    Returns:
        Summary dict with per-fold results and aggregate statistics.
    """
    n = len(df)
    chunk = n // (n_folds + 1)
    cb = config["train"]["catboost"]
    fold_results = []

    for fold_idx in range(n_folds):
        train_end = chunk * (fold_idx + 1)
        val_start = train_end
        val_end = min(train_end + chunk, n)

        if val_end <= val_start or train_end < 100:
            print(f"Fold {fold_idx + 1}: not enough data, skipping.")
            continue

        train = df.iloc[:train_end]
        val = df.iloc[val_start:val_end]

        X_train = train[FEATURE_COLUMNS].values
        y_train = train[LABEL_COL].values
        X_val   = val[FEATURE_COLUMNS].values
        y_val   = val[LABEL_COL].values

        model = CatBoostClassifier(
            loss_function=cb["loss_function"],
            iterations=cb["iterations"],
            depth=cb["depth"],
            learning_rate=cb["learning_rate"],
            random_seed=cb["random_seed"],
            verbose=0,
            class_names=CLASS_NAMES,
        )
        model.fit(Pool(X_train, y_train, feature_names=FEATURE_COLUMNS))

        preds = model.predict(X_val).flatten().astype(int)
        probas = model.predict_proba(X_val)
        acc = accuracy_score(y_val, preds)
        report = classification_report(
            y_val, preds, target_names=CLASS_NAMES, output_dict=True, zero_division=0
        )

        # Signal stats
        max_prob = probas.max(axis=1)
        signal_mask = max_prob >= signal_threshold
        n_signals = int(signal_mask.sum())
        n_long  = int(((preds == CLASS_TO_INT["UP"])   & signal_mask).sum())
        n_short = int(((preds == CLASS_TO_INT["DOWN"]) & signal_mask).sum())

        label_dist = {cls: int((y_val == CLASS_TO_INT[cls]).sum()) for cls in CLASS_NAMES}

        val_start_date = str(pd.Timestamp(val["datetime"].iloc[0]).date())
        val_end_date   = str(pd.Timestamp(val["datetime"].iloc[-1]).date())

        fold_result = {
            "fold": fold_idx + 1,
            "train_rows": len(train),
            "val_rows": len(val),
            "val_date_start": val_start_date,
            "val_date_end": val_end_date,
            "accuracy": round(float(acc), 4),
            "label_distribution": label_dist,
            "per_class": {
                cls: {
                    "precision": round(float(report[cls]["precision"]), 4),
                    "recall":    round(float(report[cls]["recall"]), 4),
                    "f1":        round(float(report[cls]["f1-score"]), 4),
                }
                for cls in CLASS_NAMES if cls in report
            },
            f"signals_above_{int(signal_threshold * 100)}pct": n_signals,
            "long_signals": n_long,
            "short_signals": n_short,
        }
        fold_results.append(fold_result)

        print(
            f"Fold {fold_idx + 1}/{n_folds}  "
            f"train={len(train):,}  val={len(val):,}  "
            f"acc={acc:.4f}  signals={n_signals}  "
            f"({val_start_date} → {val_end_date})"
        )

    avg_acc = float(np.mean([r["accuracy"] for r in fold_results])) if fold_results else 0.0

    return {
        "n_folds_completed": len(fold_results),
        "signal_threshold": signal_threshold,
        "avg_accuracy": round(avg_acc, 4),
        "folds": fold_results,
    }


def main():
    parser = argparse.ArgumentParser(description="Walk-forward validation for direction model")
    parser.add_argument("--config", default=None)
    parser.add_argument("--timeframe", default=None)
    parser.add_argument("--folds", type=int, default=None, help="Override n_folds from config")
    parser.add_argument("--threshold", type=float, default=None, help="Signal probability threshold")
    args = parser.parse_args()

    config = load_config(args.config)
    if args.timeframe:
        config["timeframe"] = args.timeframe

    wf_cfg = config.get("walk_forward", {})
    n_folds = args.folds or wf_cfg.get("n_folds", 5)
    threshold = args.threshold or wf_cfg.get("signal_threshold", 0.55)

    df = load_dataset(config)
    print(
        f"Dataset: {len(df):,} rows  "
        f"{df['datetime'].min()} → {df['datetime'].max()}\n"
        f"Walk-forward: {n_folds} folds, signal threshold={threshold}\n"
    )

    results = walk_forward_validate(df, config, n_folds=n_folds, signal_threshold=threshold)
    print(f"\nAverage accuracy across folds: {results['avg_accuracy']:.4f}")

    reports_dir = _rpath(config["output"]["reports_dir"])
    reports_dir.mkdir(parents=True, exist_ok=True)
    out_path = reports_dir / "catboost_walk_forward_v1.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Report saved: {out_path}")


if __name__ == "__main__":
    main()
