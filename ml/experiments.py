"""
Experiment runner — trains and evaluates multiple label/training configurations
and writes per-experiment JSON reports plus a summary.

Each experiment defines:
  - label mode (close or tp_sl)
  - horizon, thresholds / TP-SL percentages
  - class balance mode

The runner rebuilds labels in-memory from the existing raw features dataset,
trains a fresh model, evaluates test split and signal thresholds, then saves results.

NOTE: Building labels for tp_sl mode over 686k rows is slow (~minutes).
      Use --experiments to run a subset.

Usage (from repo root):
    python ml/experiments.py
    python ml/experiments.py --experiments 1 2 3
    python ml/experiments.py --dry-run    (prints plan, no training)

Output:
    ml/reports/experiments/<name>.json   per-experiment report
    ml/reports/experiments/summary.json  comparison table
"""
import argparse
import copy
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from catboost import CatBoostClassifier, Pool
from sklearn.metrics import accuracy_score, classification_report

from evaluate_signals import analyze_threshold, analyze_per_ticker, DEFAULT_THRESHOLDS
from features import FEATURE_COLUMNS
from labels import (
    CLASS_NAMES, CLASS_TO_INT, LABEL_COL,
    FUTURE_RETURN_COLS,
    create_labels_close, create_labels_tp_sl,
)
from train_catboost import (
    build_catboost_model, build_class_weights,
    compute_confusion_matrix, compute_feature_importance,
    time_split,
)

_ML_DIR = Path(__file__).parent
_REPO_ROOT = _ML_DIR.parent

# ---------------------------------------------------------------------------
# Experiment definitions
# ---------------------------------------------------------------------------

EXPERIMENTS = [
    {
        "id": 1,
        "name": "close_based_h3_thr025_balanced",
        "label": {
            "mode": "close",
            "horizon_candles": 3,
            "up_threshold_pct": 0.25,
            "down_threshold_pct": 0.25,
        },
        "balance_mode": "balanced",
    },
    {
        "id": 2,
        "name": "close_based_h6_thr025_balanced",
        "label": {
            "mode": "close",
            "horizon_candles": 6,
            "up_threshold_pct": 0.25,
            "down_threshold_pct": 0.25,
        },
        "balance_mode": "balanced",
    },
    {
        "id": 3,
        "name": "close_based_h6_thr020_balanced",
        "label": {
            "mode": "close",
            "horizon_candles": 6,
            "up_threshold_pct": 0.20,
            "down_threshold_pct": 0.20,
        },
        "balance_mode": "balanced",
    },
    {
        "id": 4,
        "name": "tp_sl_h6_tp030_sl020_balanced",
        "label": {
            "mode": "tp_sl",
            "horizon_candles": 6,
            "take_profit_pct": 0.30,
            "stop_loss_pct": 0.20,
        },
        "balance_mode": "balanced",
    },
    {
        "id": 5,
        "name": "tp_sl_h12_tp040_sl025_balanced",
        "label": {
            "mode": "tp_sl",
            "horizon_candles": 12,
            "take_profit_pct": 0.40,
            "stop_loss_pct": 0.25,
        },
        "balance_mode": "balanced",
    },
]


def load_config(config_path=None) -> dict:
    if config_path is None:
        config_path = _ML_DIR / "config" / "default.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def _rpath(rel: str) -> Path:
    p = Path(rel)
    return p if p.is_absolute() else (_REPO_ROOT / p).resolve()


def load_features_only(config: dict) -> pd.DataFrame:
    """Load the pre-built feature dataset without relying on its label column."""
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


def apply_labels(df: pd.DataFrame, label_cfg: dict) -> pd.DataFrame:
    """Apply label creation in-memory; drops rows with NaN labels."""
    mode = label_cfg.get("mode", "close")
    horizon = label_cfg["horizon_candles"]

    # Drop old label + future return cols if present
    drop_cols = [LABEL_COL] + FUTURE_RETURN_COLS
    df = df.drop(columns=[c for c in drop_cols if c in df.columns])

    if mode == "close":
        # Apply per ticker to avoid cross-ticker label leakage
        frames = []
        for ticker, grp in df.groupby("ticker", sort=False):
            grp = grp.sort_values("datetime").reset_index(drop=True)
            grp = create_labels_close(
                grp,
                horizon_candles=horizon,
                up_threshold_pct=label_cfg.get("up_threshold_pct", 0.25),
                down_threshold_pct=label_cfg.get("down_threshold_pct", 0.25),
                store_future_returns=True,
            )
            frames.append(grp)
        df = pd.concat(frames, ignore_index=True).sort_values("datetime").reset_index(drop=True)

    elif mode == "tp_sl":
        print("  [tp_sl] Building path-based labels — this may take several minutes…")
        frames = []
        for ticker, grp in df.groupby("ticker", sort=False):
            grp = grp.sort_values("datetime").reset_index(drop=True)
            grp = create_labels_tp_sl(
                grp,
                horizon_candles=horizon,
                take_profit_pct=label_cfg.get("take_profit_pct", 0.30),
                stop_loss_pct=label_cfg.get("stop_loss_pct", 0.20),
                flat_if_both_hit_same_candle=label_cfg.get("flat_if_both_hit_same_candle", True),
                store_future_returns=True,
            )
            frames.append(grp)
        df = pd.concat(frames, ignore_index=True).sort_values("datetime").reset_index(drop=True)
    else:
        raise ValueError(f"Unknown label mode: {mode}")

    df = df.dropna(subset=[LABEL_COL]).reset_index(drop=True)
    df[LABEL_COL] = df[LABEL_COL].astype(int)
    return df


def _best_threshold_by_precision(threshold_results: list, side: str, min_signals: int = 20) -> dict:
    """Find threshold with best precision for given side (long/short) with min_signals floor."""
    prec_key = f"{side}_precision"
    count_key = f"{side}_signals"
    best = None
    best_prec = -1.0
    for r in threshold_results:
        prec = r.get(prec_key)
        count = r.get(count_key, 0)
        if prec is not None and count >= min_signals and prec > best_prec:
            best_prec = prec
            best = {"threshold": r["threshold"], prec_key: prec, count_key: count}
    return best


def run_experiment(exp: dict, config: dict, df_features: pd.DataFrame, thresholds: list) -> dict:
    name = exp["name"]
    label_cfg = exp["label"]
    balance_mode = exp.get("balance_mode", "balanced")

    print(f"\n{'='*64}")
    print(f"Experiment: {name}")
    print(f"  label={label_cfg}  balance={balance_mode}")

    t0 = time.time()
    df = apply_labels(df_features.copy(), label_cfg)

    label_dist = {cls: int((df[LABEL_COL] == CLASS_TO_INT[cls]).sum()) for cls in CLASS_NAMES}
    print(f"  Labeled rows: {len(df):,}  dist={label_dist}")

    train_cfg = config["train"]
    train, val, test = time_split(df, train_cfg["train_frac"], train_cfg["val_frac"])

    # Build a config copy with this experiment's balance mode
    exp_config = copy.deepcopy(config)
    exp_config["train"]["class_balance"]["mode"] = balance_mode

    cb_cfg = dict(train_cfg["catboost"])
    cb_cfg["verbose"] = 0
    class_weights = build_class_weights(exp_config) if balance_mode != "none" else None
    model = build_catboost_model(cb_cfg, balance_mode, class_weights)

    X_train = train[FEATURE_COLUMNS].values
    y_train = train[LABEL_COL].values
    X_val   = val[FEATURE_COLUMNS].values
    y_val   = val[LABEL_COL].values

    model.fit(Pool(X_train, y_train, feature_names=FEATURE_COLUMNS),
              eval_set=Pool(X_val, y_val, feature_names=FEATURE_COLUMNS),
              use_best_model=True)

    # Test evaluation
    X_test = test[FEATURE_COLUMNS].values
    y_test = test[LABEL_COL].values
    preds = model.predict(X_test).flatten().astype(int)
    acc = accuracy_score(y_test, preds)
    report = classification_report(y_test, preds, target_names=CLASS_NAMES, output_dict=True, zero_division=0)

    cm_data = compute_confusion_matrix(y_test, preds)
    fi_data = compute_feature_importance(model, top_n=30)

    # Signal analysis
    probas = model.predict_proba(X_test)
    thr_results = [analyze_threshold(test, probas, thr) for thr in thresholds]
    per_ticker = analyze_per_ticker(test, probas, thresholds)

    elapsed = round(time.time() - t0, 1)
    print(f"  Test accuracy: {acc:.4f}  elapsed: {elapsed}s")
    for cls in CLASS_NAMES:
        if cls in report:
            print(f"  {cls}: precision={report[cls]['precision']:.3f}  recall={report[cls]['recall']:.3f}")

    result = {
        "experiment": name,
        "id": exp["id"],
        "label_config": label_cfg,
        "balance_mode": balance_mode,
        "labeled_rows": len(df),
        "label_distribution": label_dist,
        "test_rows": len(test),
        "test_accuracy": round(float(acc), 4),
        "test_classification_report": report,
        **cm_data,
        "signal_analysis": thr_results,
        "per_ticker": per_ticker,
        "feature_importance": fi_data,
        "elapsed_seconds": elapsed,
    }
    return result, model


def build_summary_row(result: dict) -> dict:
    rep = result.get("test_classification_report", {})
    thr_list = result.get("signal_analysis", [])

    best_long  = _best_threshold_by_precision(thr_list, "long",  min_signals=20)
    best_short = _best_threshold_by_precision(thr_list, "short", min_signals=20)

    # Coverage at best long threshold
    coverage = None
    if best_long:
        thr_match = next((r for r in thr_list if r["threshold"] == best_long["threshold"]), None)
        if thr_match:
            coverage = thr_match.get("coverage_pct")

    return {
        "experiment": result["experiment"],
        "label_mode": result["label_config"].get("mode"),
        "horizon": result["label_config"].get("horizon_candles"),
        "balance_mode": result["balance_mode"],
        "test_accuracy": result["test_accuracy"],
        "UP_precision":   round(float(rep.get("UP",   {}).get("precision", 0)), 4),
        "UP_recall":      round(float(rep.get("UP",   {}).get("recall",    0)), 4),
        "DOWN_precision": round(float(rep.get("DOWN", {}).get("precision", 0)), 4),
        "DOWN_recall":    round(float(rep.get("DOWN", {}).get("recall",    0)), 4),
        "FLAT_precision": round(float(rep.get("FLAT", {}).get("precision", 0)), 4),
        "FLAT_recall":    round(float(rep.get("FLAT", {}).get("recall",    0)), 4),
        "best_long_threshold":  best_long,
        "best_short_threshold": best_short,
        "coverage_pct_at_best_long": coverage,
    }


def main():
    parser = argparse.ArgumentParser(description="Run ML experiments for direction model")
    parser.add_argument("--config", default=None)
    parser.add_argument("--timeframe", default=None)
    parser.add_argument(
        "--experiments",
        nargs="+",
        type=int,
        default=None,
        help="Experiment IDs to run (default: all). E.g. --experiments 1 2 3",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print plan without training",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    if args.timeframe:
        config["timeframe"] = args.timeframe

    thresholds = config.get("signal_thresholds", DEFAULT_THRESHOLDS)

    selected = EXPERIMENTS
    if args.experiments:
        ids = set(args.experiments)
        selected = [e for e in EXPERIMENTS if e["id"] in ids]
        if not selected:
            print(f"No experiments matched IDs: {ids}")
            sys.exit(1)

    print(f"Experiments to run: {[e['name'] for e in selected]}")
    if args.dry_run:
        for e in selected:
            print(f"  [{e['id']}] {e['name']}  label={e['label']}  balance={e['balance_mode']}")
        return

    reports_dir = _rpath(config["output"]["reports_dir"]) / "experiments"
    reports_dir.mkdir(parents=True, exist_ok=True)

    print("\nLoading feature dataset…")
    df_features = load_features_only(config)
    print(f"  {len(df_features):,} rows loaded")

    summary_rows = []
    for exp in selected:
        try:
            result, _ = run_experiment(exp, config, df_features, thresholds)
            out_path = reports_dir / f"{exp['name']}.json"
            with open(out_path, "w") as f:
                json.dump(result, f, indent=2)
            print(f"  Saved: {out_path}")
            summary_rows.append(build_summary_row(result))
        except Exception as exc:
            print(f"  [ERROR] {exp['name']}: {exc}")
            import traceback; traceback.print_exc()

    summary = {"experiments": summary_rows}
    summary_path = reports_dir / "summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\nSummary saved: {summary_path}")

    # Print comparison table
    print("\n--- EXPERIMENT SUMMARY ---")
    header = f"{'Name':<45}  {'ACC':>5}  {'UP_P':>5}  {'UP_R':>5}  {'DN_P':>5}  {'DN_R':>5}"
    print(header)
    for row in summary_rows:
        print(
            f"{row['experiment']:<45}  "
            f"{row['test_accuracy']:>5.3f}  "
            f"{row['UP_precision']:>5.3f}  "
            f"{row['UP_recall']:>5.3f}  "
            f"{row['DOWN_precision']:>5.3f}  "
            f"{row['DOWN_recall']:>5.3f}"
        )


if __name__ == "__main__":
    main()
