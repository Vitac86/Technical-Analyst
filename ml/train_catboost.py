"""
Train CatBoost MultiClass direction model on the processed dataset.

Trains two variants by default:
  v1_baseline — no class balancing (original behaviour)
  v2_balanced — class weights to handle FLAT-heavy imbalance

Split strategy: time-based (oldest 70% train, next 15% val, latest 15% test).
No random shuffling — prevents look-ahead bias in a time-series model.

Outputs per variant (example for v2_balanced):
  ml/models/catboost_direction_v2_balanced.cbm
  ml/models/catboost_direction_v2_balanced_manifest.json
  ml/reports/catboost_direction_v2_balanced_metrics.json

Usage (from repo root):
    python ml/train_catboost.py
    python ml/train_catboost.py --variant balanced
    python ml/train_catboost.py --variant baseline
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
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix

from features import FEATURE_COLUMNS
from labels import CLASS_ID_TO_NAME, CLASS_NAMES, CLASS_TO_INT, LABEL_COL

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
            f"Dataset not found at {parquet_path} or {csv_path}\n"
            "Run build_dataset.py first."
        )
    return df.sort_values("datetime").reset_index(drop=True)


def time_split(df: pd.DataFrame, train_frac: float, val_frac: float):
    n = len(df)
    i_train = int(n * train_frac)
    i_val = int(n * (train_frac + val_frac))
    return df.iloc[:i_train].copy(), df.iloc[i_train:i_val].copy(), df.iloc[i_val:].copy()


def build_class_weights(config: dict) -> dict:
    """
    Return dict {class_int: weight} or None based on class_balance config.

    balanced → None (CatBoost auto_class_weights="Balanced" handles it)
    manual   → explicit numeric weights {0: w_down, 1: w_flat, 2: w_up}
    none     → None
    """
    cb_cfg = config.get("train", {}).get("class_balance", {})
    mode = cb_cfg.get("mode", "none")
    if mode == "manual":
        mw = cb_cfg.get("manual_weights", {})
        return {
            CLASS_TO_INT["DOWN"]: float(mw.get("DOWN", 1.0)),
            CLASS_TO_INT["FLAT"]: float(mw.get("FLAT", 1.0)),
            CLASS_TO_INT["UP"]:   float(mw.get("UP",   1.0)),
        }
    return None


def build_catboost_model(cb_cfg: dict, balance_mode: str, class_weights: dict) -> CatBoostClassifier:
    """Instantiate CatBoostClassifier with appropriate balancing parameters."""
    kwargs = dict(
        loss_function=cb_cfg["loss_function"],
        iterations=cb_cfg["iterations"],
        depth=cb_cfg["depth"],
        learning_rate=cb_cfg["learning_rate"],
        eval_metric=cb_cfg["eval_metric"],
        random_seed=cb_cfg["random_seed"],
        verbose=cb_cfg["verbose"],
    )
    if balance_mode == "balanced":
        kwargs["auto_class_weights"] = "Balanced"
    elif balance_mode == "manual" and class_weights:
        # CatBoost class_weights expects a list ordered by class index
        n_classes = max(class_weights.keys()) + 1
        kwargs["class_weights"] = [class_weights.get(i, 1.0) for i in range(n_classes)]
    return CatBoostClassifier(**kwargs)


def compute_confusion_matrix(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    cm = confusion_matrix(y_true, y_pred, labels=[0, 1, 2])
    cm_norm = cm.astype(float)
    row_sums = cm_norm.sum(axis=1, keepdims=True)
    cm_norm = np.where(row_sums > 0, cm_norm / row_sums, 0.0)

    labels = ["DOWN", "FLAT", "UP"]
    return {
        "labels": labels,
        "confusion_matrix": cm.tolist(),
        "confusion_matrix_normalized": [[round(v, 4) for v in row] for row in cm_norm.tolist()],
    }


def compute_per_ticker_metrics(
    df_split: pd.DataFrame,
    model: CatBoostClassifier,
) -> dict:
    results = {}
    tickers = sorted(df_split["ticker"].unique()) if "ticker" in df_split.columns else []
    for ticker in tickers:
        mask = df_split["ticker"] == ticker
        sub = df_split[mask]
        if len(sub) < 10:
            continue
        X = sub[FEATURE_COLUMNS].values
        y = sub[LABEL_COL].values
        preds = model.predict(X).flatten().astype(int)
        report = classification_report(
            y, preds, target_names=CLASS_NAMES, output_dict=True, zero_division=0
        )
        label_dist = {cls: int((y == CLASS_TO_INT[cls]).sum()) for cls in CLASS_NAMES}
        results[ticker] = {
            "rows": len(sub),
            "label_distribution": label_dist,
            "per_class": {
                cls: {
                    "precision": round(float(report[cls]["precision"]), 4),
                    "recall":    round(float(report[cls]["recall"]), 4),
                    "f1":        round(float(report[cls]["f1-score"]), 4),
                }
                for cls in CLASS_NAMES if cls in report
            },
        }
    return results


def evaluate(
    model: CatBoostClassifier,
    df_split: pd.DataFrame,
    split_name: str,
) -> dict:
    X = df_split[FEATURE_COLUMNS].values
    y = df_split[LABEL_COL].values
    preds = model.predict(X).flatten().astype(int)
    acc = accuracy_score(y, preds)
    report = classification_report(y, preds, target_names=CLASS_NAMES, output_dict=True, zero_division=0)
    print(f"\n--- {split_name.upper()} ---")
    print(f"Accuracy: {acc:.4f}")
    print(classification_report(y, preds, target_names=CLASS_NAMES, zero_division=0))

    cm_data = compute_confusion_matrix(y, preds)
    per_ticker = compute_per_ticker_metrics(df_split, model)

    return {
        "accuracy": round(float(acc), 4),
        "class_id_mapping": CLASS_ID_TO_NAME,
        "classification_report": report,
        **cm_data,
        "per_ticker": per_ticker,
    }


def compute_feature_importance(model: CatBoostClassifier, top_n: int = 30) -> dict:
    all_imp = dict(
        sorted(
            zip(FEATURE_COLUMNS, model.get_feature_importance()),
            key=lambda x: x[1],
            reverse=True,
        )
    )
    all_imp = {k: round(float(v), 4) for k, v in all_imp.items()}
    top = dict(list(all_imp.items())[:top_n])
    return {"all_features": all_imp, "top_features": top}


def build_manifest(config: dict, model_version: str, feature_importances: dict, balance_mode: str) -> dict:
    lbl = config["label"]
    cb = config["train"]["catboost"]
    return {
        "modelVersion": model_version,
        "modelType": "catboost_multiclass",
        "target": "direction_next_candles",
        "classes": CLASS_NAMES,
        "classIdMapping": CLASS_ID_TO_NAME,
        "featureNames": FEATURE_COLUMNS,
        "featureCount": len(FEATURE_COLUMNS),
        "labelMode": lbl.get("mode", "close"),
        "horizonCandles": lbl["horizon_candles"],
        "upThresholdPct": lbl.get("up_threshold_pct"),
        "downThresholdPct": lbl.get("down_threshold_pct"),
        "classBalanceMode": balance_mode,
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
        "featureImportances": feature_importances,
        "inferenceNotes": (
            "Trained on liquid MOEX shares (TQBR board). "
            "Apply primarily to similar liquid MOEX equity instruments. "
            "Not valid for FX, futures, bonds, or illiquid instruments."
        ),
        "createdAt": str(date.today()),
        "status": "experimental",
        "disclaimer": "Not financial advice. Experimental research model only.",
    }


def train_variant(
    name: str,
    balance_mode: str,
    config: dict,
    train: pd.DataFrame,
    val: pd.DataFrame,
    test: pd.DataFrame,
    models_dir: Path,
    reports_dir: Path,
) -> CatBoostClassifier:
    print(f"\n{'='*60}")
    print(f"Training variant: {name}  (balance_mode={balance_mode})")
    print(f"{'='*60}")

    cb_cfg = config["train"]["catboost"]
    class_weights = build_class_weights(config) if balance_mode in ("balanced", "manual") else None
    model = build_catboost_model(cb_cfg, balance_mode, class_weights)

    X_train = train[FEATURE_COLUMNS].values
    y_train = train[LABEL_COL].values
    X_val   = val[FEATURE_COLUMNS].values
    y_val   = val[LABEL_COL].values

    train_pool = Pool(X_train, y_train, feature_names=FEATURE_COLUMNS)
    val_pool   = Pool(X_val,   y_val,   feature_names=FEATURE_COLUMNS)
    model.fit(train_pool, eval_set=val_pool, use_best_model=True)

    metrics = {
        "variant": name,
        "balance_mode": balance_mode,
        "val":  evaluate(model, val,  "val"),
        "test": evaluate(model, test, "test"),
    }

    fi = compute_feature_importance(model, top_n=30)
    print(f"\nTop-5 features: {list(fi['top_features'].keys())[:5]}")

    # Save model
    model_path = models_dir / f"{name}.cbm"
    model.save_model(str(model_path))
    print(f"Model saved:    {model_path}")

    # Save manifest
    manifest = build_manifest(config, name, fi, balance_mode)
    manifest_path = models_dir / f"{name}_manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"Manifest saved: {manifest_path}")

    # Save metrics report
    metrics_path = reports_dir / f"{name}_metrics.json"
    with open(metrics_path, "w") as f:
        json.dump(metrics, f, indent=2)
    print(f"Metrics saved:  {metrics_path}")

    return model


def main():
    parser = argparse.ArgumentParser(description="Train CatBoost direction model (v2)")
    parser.add_argument("--config", default=None)
    parser.add_argument("--timeframe", default=None)
    parser.add_argument(
        "--variant",
        default="both",
        choices=["both", "baseline", "balanced"],
        help="Which variant to train (default: both)",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    if args.timeframe:
        config["timeframe"] = args.timeframe

    df = load_dataset(config)
    print(f"Loaded dataset: {len(df):,} rows, {len(FEATURE_COLUMNS)} features")
    print(f"Date range:     {df['datetime'].min()} -> {df['datetime'].max()}")
    print(f"Tickers:        {sorted(df['ticker'].unique().tolist())}")

    label_dist = {cls: int((df[LABEL_COL] == CLASS_TO_INT[cls]).sum()) for cls in CLASS_NAMES}
    print(f"Label dist:     {label_dist}")

    train_cfg = config["train"]
    train, val, test = time_split(df, train_cfg["train_frac"], train_cfg["val_frac"])
    print(f"Split:          train={len(train):,}  val={len(val):,}  test={len(test):,}")

    models_dir = _rpath(config["output"]["models_dir"])
    reports_dir = _rpath(config["output"]["reports_dir"])
    models_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    variants = []
    if args.variant in ("both", "baseline"):
        variants.append(("catboost_direction_v1_baseline", "none"))
    if args.variant in ("both", "balanced"):
        variants.append(("catboost_direction_v2_balanced", "balanced"))

    for name, mode in variants:
        train_variant(name, mode, config, train, val, test, models_dir, reports_dir)

    print("\nDone. Next step: python ml/evaluate_signals.py")


if __name__ == "__main__":
    main()
