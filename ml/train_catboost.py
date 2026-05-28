"""
Train CatBoost MultiClass direction model on the processed dataset.

Split strategy: time-based (oldest 70% train, next 15% val, latest 15% test).
No random shuffling — this prevents look-ahead bias in a time-series model.

Outputs:
  ml/models/catboost_direction_v1.cbm        — trained model binary
  ml/models/catboost_direction_v1_manifest.json  — model metadata
  ml/reports/catboost_direction_v1_metrics.json  — val/test metrics

Usage (from repo root):
    python ml/train_catboost.py
    python ml/train_catboost.py --timeframe 15m
"""
import argparse
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from catboost import CatBoostClassifier, Pool
from sklearn.metrics import accuracy_score, classification_report

from features import FEATURE_COLUMNS
from labels import CLASS_ID_TO_NAME, CLASS_NAMES, LABEL_COL

_ML_DIR = Path(__file__).parent
_REPO_ROOT = _ML_DIR.parent

MODEL_VERSION = "catboost_direction_v1"


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
            f"Dataset not found at {parquet_path} or {csv_path}\n"
            "Run build_dataset.py first."
        )
    return df.sort_values("datetime").reset_index(drop=True)


def time_split(df: pd.DataFrame, train_frac: float, val_frac: float):
    """Split DataFrame into train / val / test by position (time order)."""
    n = len(df)
    i_train = int(n * train_frac)
    i_val = int(n * (train_frac + val_frac))
    return df.iloc[:i_train].copy(), df.iloc[i_train:i_val].copy(), df.iloc[i_val:].copy()


def evaluate(model: CatBoostClassifier, X: np.ndarray, y: np.ndarray, split_name: str) -> dict:
    preds = model.predict(X).flatten().astype(int)
    acc = accuracy_score(y, preds)
    report = classification_report(y, preds, target_names=CLASS_NAMES, output_dict=True)
    print(f"\n--- {split_name.upper()} ---")
    print(f"Accuracy: {acc:.4f}")
    print(classification_report(y, preds, target_names=CLASS_NAMES))
    return {
        "accuracy": round(float(acc), 4),
        "class_id_mapping": CLASS_ID_TO_NAME,
        "classification_report": report,
    }


def build_manifest(config: dict, feature_importances: dict) -> dict:
    lbl = config["label"]
    cb = config["train"]["catboost"]
    return {
        "modelVersion": MODEL_VERSION,
        "modelType": "catboost_multiclass",
        "target": "direction_next_candles",
        "classes": CLASS_NAMES,
        "featureNames": FEATURE_COLUMNS,
        "featureCount": len(FEATURE_COLUMNS),
        "horizonCandles": lbl["horizon_candles"],
        "upThresholdPct": lbl["up_threshold_pct"],
        "downThresholdPct": lbl["down_threshold_pct"],
        "trainingConfig": {
            "engine": config["engine"],
            "market": config["market"],
            "board": config["board"],
            "timeframe": config["timeframe"],
            "tickers": config["tickers"],
            "dateFrom": config["date_from"],
            "dateTo": config["date_to"],
            "catboost": {k: v for k, v in cb.items() if k != "verbose"},
        },
        "topFeatureImportances": feature_importances,
        "inferenceNotes": (
            "Trained on liquid MOEX shares (TQBR board). "
            "Apply primarily to similar liquid MOEX equity instruments. "
            "Not valid for FX, futures, bonds, or illiquid instruments."
        ),
        "createdAt": str(date.today()),
        "status": "experimental",
        "disclaimer": "Not financial advice. Experimental research model only.",
    }


def main():
    parser = argparse.ArgumentParser(description="Train CatBoost direction model")
    parser.add_argument("--config", default=None)
    parser.add_argument("--timeframe", default=None, help="Override timeframe from config")
    args = parser.parse_args()

    config = load_config(args.config)
    if args.timeframe:
        config["timeframe"] = args.timeframe

    df = load_dataset(config)
    print(f"Loaded dataset: {len(df):,} rows, {len(FEATURE_COLUMNS)} features")
    print(f"Date range:     {df['datetime'].min()} -> {df['datetime'].max()}")
    print(f"Tickers:        {sorted(df['ticker'].unique().tolist())}")

    train_cfg = config["train"]
    train, val, test = time_split(df, train_cfg["train_frac"], train_cfg["val_frac"])
    print(
        f"\nSplit (time-based):  train={len(train):,}  val={len(val):,}  test={len(test):,}"
    )

    X_train = train[FEATURE_COLUMNS].values
    y_train = train[LABEL_COL].values
    X_val   = val[FEATURE_COLUMNS].values
    y_val   = val[LABEL_COL].values
    X_test  = test[FEATURE_COLUMNS].values
    y_test  = test[LABEL_COL].values

    cb = config["train"]["catboost"]
    model = CatBoostClassifier(
        loss_function=cb["loss_function"],
        iterations=cb["iterations"],
        depth=cb["depth"],
        learning_rate=cb["learning_rate"],
        eval_metric=cb["eval_metric"],
        random_seed=cb["random_seed"],
        verbose=cb["verbose"],
    )

    print("\nTraining…")
    train_pool = Pool(X_train, y_train, feature_names=FEATURE_COLUMNS)
    val_pool   = Pool(X_val,   y_val,   feature_names=FEATURE_COLUMNS)
    model.fit(train_pool, eval_set=val_pool, use_best_model=True)

    # Evaluate on val and test
    metrics = {}
    metrics["val"]  = evaluate(model, X_val,  y_val,  "val")
    metrics["test"] = evaluate(model, X_test, y_test, "test")

    # Feature importances (top 15)
    importances = dict(
        sorted(
            zip(FEATURE_COLUMNS, model.get_feature_importance()),
            key=lambda x: x[1],
            reverse=True,
        )[:15]
    )
    importances = {k: round(float(v), 2) for k, v in importances.items()}
    print(f"\nTop feature importances: {list(importances.keys())[:5]}")

    # Save model
    models_dir = _rpath(config["output"]["models_dir"])
    models_dir.mkdir(parents=True, exist_ok=True)
    model_path = models_dir / f"{MODEL_VERSION}.cbm"
    model.save_model(str(model_path))
    print(f"\nModel saved:   {model_path}")

    # Save manifest
    manifest = build_manifest(config, importances)
    manifest_path = models_dir / f"{MODEL_VERSION}_manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"Manifest saved: {manifest_path}")

    # Save metrics report
    reports_dir = _rpath(config["output"]["reports_dir"])
    reports_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = reports_dir / f"{MODEL_VERSION}_metrics.json"
    with open(metrics_path, "w") as f:
        json.dump(metrics, f, indent=2)
    print(f"Metrics saved:  {metrics_path}")


if __name__ == "__main__":
    main()
