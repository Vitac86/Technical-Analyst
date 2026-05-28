"""
Walk-forward validation for the direction model.

Uses an expanding window: each fold trains on all data before the validation
window and evaluates on the next fixed-size chunk.

Supports the same class_balance modes as train_catboost.py (none/balanced/manual).
Pass --model to validate a specific saved .cbm file instead of re-training per fold.

Usage (from repo root):
    python ml/validate_walk_forward.py
    python ml/validate_walk_forward.py --folds 6 --threshold 0.60
    python ml/validate_walk_forward.py --model catboost_direction_v2_balanced
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
from train_catboost import build_catboost_model, build_class_weights

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
    pretrained_model: CatBoostClassifier = None,
) -> dict:
    """
    Expanding-window walk-forward validation.

    If pretrained_model is supplied, uses it for all folds (no re-training).
    Otherwise re-trains a fresh model per fold using config settings.
    """
    n = len(df)
    chunk = n // (n_folds + 1)
    cb_cfg = config["train"]["catboost"]
    balance_mode = config.get("train", {}).get("class_balance", {}).get("mode", "none")
    class_weights = build_class_weights(config) if balance_mode != "none" else None

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

        if pretrained_model is not None:
            model = pretrained_model
        else:
            fold_cb = dict(cb_cfg)
            fold_cb["verbose"] = 0
            model = build_catboost_model(fold_cb, balance_mode, class_weights)
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

        # Precision of signals that actually fired
        up_idx = CLASS_TO_INT["UP"]
        dn_idx = CLASS_TO_INT["DOWN"]
        long_correct  = int(((preds == up_idx) & signal_mask & (y_val == up_idx)).sum())
        short_correct = int(((preds == dn_idx) & signal_mask & (y_val == dn_idx)).sum())
        long_prec  = round(long_correct  / n_long,  4) if n_long  > 0 else None
        short_prec = round(short_correct / n_short, 4) if n_short > 0 else None

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
            "long_precision_at_threshold": long_prec,
            "short_precision_at_threshold": short_prec,
        }
        fold_results.append(fold_result)

        print(
            f"Fold {fold_idx + 1}/{n_folds}  "
            f"train={len(train):,}  val={len(val):,}  "
            f"acc={acc:.4f}  signals={n_signals}  "
            f"long_prec={long_prec}  short_prec={short_prec}  "
            f"({val_start_date} → {val_end_date})"
        )

    avg_acc = float(np.mean([r["accuracy"] for r in fold_results])) if fold_results else 0.0

    return {
        "n_folds_completed": len(fold_results),
        "signal_threshold": signal_threshold,
        "balance_mode": balance_mode,
        "avg_accuracy": round(avg_acc, 4),
        "folds": fold_results,
    }


def main():
    parser = argparse.ArgumentParser(description="Walk-forward validation for direction model")
    parser.add_argument("--config", default=None)
    parser.add_argument("--timeframe", default=None)
    parser.add_argument("--folds", type=int, default=None)
    parser.add_argument("--threshold", type=float, default=None)
    parser.add_argument(
        "--model",
        default=None,
        help="Model version name to load (e.g. catboost_direction_v2_balanced). "
             "If omitted, re-trains per fold.",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    if args.timeframe:
        config["timeframe"] = args.timeframe

    wf_cfg = config.get("walk_forward", {})
    n_folds = args.folds or wf_cfg.get("n_folds", 5)
    threshold = args.threshold or wf_cfg.get("signal_threshold", 0.55)

    pretrained = None
    model_name = "retrain_per_fold"
    if args.model:
        model_path = _rpath(config["output"]["models_dir"]) / f"{args.model}.cbm"
        if not model_path.exists():
            raise FileNotFoundError(f"Model not found: {model_path}")
        pretrained = CatBoostClassifier()
        pretrained.load_model(str(model_path))
        model_name = args.model
        print(f"Using pretrained model: {model_path}")

    df = load_dataset(config)
    print(
        f"Dataset: {len(df):,} rows  "
        f"{df['datetime'].min()} → {df['datetime'].max()}\n"
        f"Walk-forward: {n_folds} folds, threshold={threshold}, model={model_name}\n"
    )

    results = walk_forward_validate(
        df, config, n_folds=n_folds,
        signal_threshold=threshold,
        pretrained_model=pretrained,
    )
    results["model_name"] = model_name
    print(f"\nAverage accuracy across folds: {results['avg_accuracy']:.4f}")

    reports_dir = _rpath(config["output"]["reports_dir"])
    reports_dir.mkdir(parents=True, exist_ok=True)
    suffix = args.model or "retrain"
    out_path = reports_dir / f"catboost_walk_forward_{suffix}.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Report saved: {out_path}")


if __name__ == "__main__":
    main()
