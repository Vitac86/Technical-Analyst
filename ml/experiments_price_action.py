"""
CatBoost experiments augmented with fractal / price-action features.

Experiments
-----------
1. catboost_pa_close_h6_thr020_balanced
   Multiclass  close label  horizon=6  thr=0.20%  balanced weights
   Feature set: original 30 tech features + price action features

2. catboost_pa_tp_sl_h12_tp040_sl025_balanced
   Multiclass  tp_sl label  horizon=12  TP=0.40%  SL=0.25%  balanced
   Feature set: original 30 + price action

3. catboost_pa_short_focused_tp_sl_h12
   Binary classifier: SHORT_SETUP (label==DOWN) vs NOT_SHORT
   tp_sl label  horizon=12  TP=0.40%  SL=0.25%  balanced
   Motivated by prior finding that SHORT signals are more promising.

Usage (from repo root):
    python ml\\experiments_price_action.py
    python ml\\experiments_price_action.py --experiments 1 2
    python ml\\experiments_price_action.py --dry-run

Output:
    ml/reports/price_action/<name>.json    per-experiment report
    ml/reports/price_action/summary.json   comparison + cross-check vs fractal backtest
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
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
)

_ML_DIR    = Path(__file__).parent
_REPO_ROOT = _ML_DIR.parent
sys.path.insert(0, str(_ML_DIR))

from evaluate_signals import analyze_threshold, analyze_per_ticker, DEFAULT_THRESHOLDS
from features import FEATURE_COLUMNS
from fractal_features import add_confirmed_fractals_grouped
from labels import (
    CLASS_NAMES, CLASS_TO_INT, LABEL_COL, FUTURE_RETURN_COLS,
    create_labels_close, create_labels_tp_sl,
)
from price_action import add_price_action_features_grouped, PRICE_ACTION_FEATURE_COLUMNS
from train_catboost import (
    build_catboost_model, build_class_weights,
    compute_confusion_matrix, compute_feature_importance,
    time_split,
)

# ---------------------------------------------------------------------------
# Experiment definitions
# ---------------------------------------------------------------------------

EXPERIMENTS = [
    {
        "id":   1,
        "name": "catboost_pa_close_h6_thr020_balanced",
        "label": {
            "mode":             "close",
            "horizon_candles":  6,
            "up_threshold_pct": 0.20,
            "down_threshold_pct": 0.20,
        },
        "balance_mode": "balanced",
        "classifier":   "multiclass",
    },
    {
        "id":   2,
        "name": "catboost_pa_tp_sl_h12_tp040_sl025_balanced",
        "label": {
            "mode":             "tp_sl",
            "horizon_candles":  12,
            "take_profit_pct":  0.40,
            "stop_loss_pct":    0.25,
        },
        "balance_mode": "balanced",
        "classifier":   "multiclass",
    },
    {
        "id":   3,
        "name": "catboost_pa_short_focused_tp_sl_h12",
        "label": {
            "mode":             "tp_sl",
            "horizon_candles":  12,
            "take_profit_pct":  0.40,
            "stop_loss_pct":    0.25,
        },
        "balance_mode": "balanced",   # applied to binary labels
        "classifier":   "binary",     # SHORT_SETUP vs NOT_SHORT
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_config(config_path=None) -> dict:
    if config_path is None:
        config_path = _ML_DIR / "config" / "default.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def _rpath(rel: str, base: Path = _REPO_ROOT) -> Path:
    p = Path(rel)
    return p if p.is_absolute() else (base / p).resolve()


def load_features_only(config: dict) -> pd.DataFrame:
    tf          = config["timeframe"]
    proc_dir    = _rpath(config["output"]["processed_dir"])
    parquet     = proc_dir / f"dataset_{tf}.parquet"
    csv_path    = parquet.with_suffix(".csv")

    if parquet.exists():
        df = pd.read_parquet(parquet)
    elif csv_path.exists():
        df = pd.read_csv(csv_path, parse_dates=["datetime"])
    else:
        raise FileNotFoundError(
            f"Dataset not found: {parquet}\nRun python ml\\build_dataset.py first."
        )
    return df.sort_values("datetime").reset_index(drop=True)


def apply_labels(df: pd.DataFrame, label_cfg: dict) -> pd.DataFrame:
    mode    = label_cfg.get("mode", "close")
    horizon = label_cfg["horizon_candles"]

    drop_cols = [LABEL_COL] + FUTURE_RETURN_COLS
    df = df.drop(columns=[c for c in drop_cols if c in df.columns])

    frames = []
    for ticker, grp in df.groupby("ticker", sort=False):
        grp = grp.sort_values("datetime").reset_index(drop=True)
        if mode == "close":
            grp = create_labels_close(
                grp,
                horizon_candles=horizon,
                up_threshold_pct=label_cfg.get("up_threshold_pct", 0.25),
                down_threshold_pct=label_cfg.get("down_threshold_pct", 0.25),
                store_future_returns=True,
            )
        elif mode == "tp_sl":
            grp = create_labels_tp_sl(
                grp,
                horizon_candles=horizon,
                take_profit_pct=label_cfg.get("take_profit_pct", 0.30),
                stop_loss_pct=label_cfg.get("stop_loss_pct", 0.20),
                flat_if_both_hit_same_candle=label_cfg.get("flat_if_both_hit_same_candle", True),
                store_future_returns=True,
            )
        else:
            raise ValueError(f"Unknown label mode: {mode}")
        frames.append(grp)

    df = pd.concat(frames, ignore_index=True).sort_values("datetime").reset_index(drop=True)
    df = df.dropna(subset=[LABEL_COL]).reset_index(drop=True)
    df[LABEL_COL] = df[LABEL_COL].astype(int)
    return df


def add_pa_features(df: pd.DataFrame) -> pd.DataFrame:
    df = add_confirmed_fractals_grouped(df)
    df = add_price_action_features_grouped(df)
    return df


def drop_invalid_pa_rows(df: pd.DataFrame, feature_cols: list) -> pd.DataFrame:
    feat = df[feature_cols].replace([np.inf, -np.inf], np.nan)
    bad  = feat.isna().any(axis=1)
    n_dropped = int(bad.sum())
    if n_dropped:
        print(f"  Dropping {n_dropped} rows with NaN/inf in PA features.")
    return df[~bad].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Experiment runner
# ---------------------------------------------------------------------------

def run_experiment(
    exp: dict,
    config: dict,
    df_features: pd.DataFrame,
    thresholds: list,
) -> dict:
    name        = exp["name"]
    label_cfg   = exp["label"]
    balance_mode = exp.get("balance_mode", "balanced")
    classifier  = exp.get("classifier", "multiclass")

    print(f"\n{'='*64}")
    print(f"Experiment: {name}  [{classifier}]")
    print(f"  label={label_cfg}  balance={balance_mode}")

    t0 = time.time()

    # 1. Labels
    if label_cfg["mode"] == "tp_sl":
        print("  [tp_sl] Building path-based labels — may take several minutes…")
    df = apply_labels(df_features.copy(), label_cfg)
    label_dist = {cls: int((df[LABEL_COL] == CLASS_TO_INT[cls]).sum()) for cls in CLASS_NAMES}
    print(f"  Labeled rows: {len(df):,}  dist={label_dist}")

    # 2. Price action features
    print("  Adding fractal + price action features…")
    df = add_pa_features(df)

    # 3. Combined feature list
    all_feature_cols = FEATURE_COLUMNS + [
        c for c in PRICE_ACTION_FEATURE_COLUMNS if c not in FEATURE_COLUMNS
    ]

    # Drop rows where any feature is NaN/inf
    df = drop_invalid_pa_rows(df, all_feature_cols)
    print(f"  After PA feature drop: {len(df):,} rows")

    # 4. Time split
    train_cfg = config["train"]
    train, val, test = time_split(df, train_cfg["train_frac"], train_cfg["val_frac"])

    # 5. Build model
    cb_cfg = dict(train_cfg["catboost"])
    cb_cfg["verbose"] = 0

    if classifier == "binary":
        # Convert to binary: 1 = SHORT_SETUP (DOWN=0 in original), 0 = NOT_SHORT
        y_train_bin = (train[LABEL_COL].values == CLASS_TO_INT["DOWN"]).astype(int)
        y_val_bin   = (val[LABEL_COL].values   == CLASS_TO_INT["DOWN"]).astype(int)
        y_test_bin  = (test[LABEL_COL].values  == CLASS_TO_INT["DOWN"]).astype(int)

        bin_cfg = dict(cb_cfg)
        bin_cfg["loss_function"] = "Logloss"
        bin_cfg.pop("classes_count", None)

        pos_count = y_train_bin.sum()
        neg_count = len(y_train_bin) - pos_count
        scale_pos = neg_count / pos_count if pos_count > 0 else 1.0

        model = CatBoostClassifier(
            **bin_cfg,
            scale_pos_weight=scale_pos,
        )
        X_train = train[all_feature_cols].values
        X_val   = val[all_feature_cols].values

        model.fit(
            Pool(X_train, y_train_bin, feature_names=all_feature_cols),
            eval_set=Pool(X_val, y_val_bin, feature_names=all_feature_cols),
            use_best_model=True,
        )

        X_test  = test[all_feature_cols].values
        preds   = model.predict(X_test).flatten().astype(int)
        probas  = model.predict_proba(X_test)  # shape (n, 2)

        prec  = round(float(precision_score(y_test_bin, preds, zero_division=0)), 4)
        rec   = round(float(recall_score(y_test_bin, preds, zero_division=0)), 4)
        f1    = round(float(f1_score(y_test_bin, preds, zero_division=0)), 4)
        acc   = round(float(accuracy_score(y_test_bin, preds)), 4)

        report = {
            "SHORT_precision": prec,
            "SHORT_recall":    rec,
            "SHORT_f1":        f1,
            "accuracy":        acc,
            "class_0_count":   int((y_test_bin == 0).sum()),
            "class_1_count":   int((y_test_bin == 1).sum()),
        }

        fi_names = model.feature_names_
        fi_vals  = model.get_feature_importance()
        fi_pairs = sorted(zip(fi_names, fi_vals), key=lambda x: x[1], reverse=True)
        fi_data  = [{"feature": n, "importance": round(float(v), 4)} for n, v in fi_pairs]

        elapsed = round(time.time() - t0, 1)
        print(f"  SHORT precision={prec:.3f}  recall={rec:.3f}  F1={f1:.3f}  elapsed={elapsed}s")

        result = {
            "experiment":       name,
            "id":               exp["id"],
            "classifier":       "binary",
            "label_config":     label_cfg,
            "balance_mode":     balance_mode,
            "scale_pos_weight": round(float(scale_pos), 3),
            "labeled_rows":     len(df),
            "label_distribution": label_dist,
            "test_rows":        len(test),
            "binary_report":    report,
            "feature_importance": fi_data,
            "elapsed_seconds":  elapsed,
        }
        return result, model

    # --- Multiclass path ---
    exp_config = copy.deepcopy(config)
    exp_config["train"]["class_balance"]["mode"] = balance_mode
    class_weights = build_class_weights(exp_config) if balance_mode != "none" else None
    model = build_catboost_model(cb_cfg, balance_mode, class_weights)

    X_train = train[all_feature_cols].values
    y_train = train[LABEL_COL].values
    X_val   = val[all_feature_cols].values
    y_val   = val[LABEL_COL].values

    model.fit(
        Pool(X_train, y_train, feature_names=all_feature_cols),
        eval_set=Pool(X_val,   y_val,   feature_names=all_feature_cols),
        use_best_model=True,
    )

    X_test  = test[all_feature_cols].values
    y_test  = test[LABEL_COL].values
    preds   = model.predict(X_test).flatten().astype(int)
    acc     = accuracy_score(y_test, preds)
    report  = classification_report(
        y_test, preds, target_names=CLASS_NAMES, output_dict=True, zero_division=0
    )

    cm_data = compute_confusion_matrix(y_test, preds)
    fi_data = compute_feature_importance(model, top_n=len(all_feature_cols))

    probas      = model.predict_proba(X_test)
    thr_results = [analyze_threshold(test, probas, thr) for thr in thresholds]
    per_ticker  = analyze_per_ticker(test, probas, thresholds)

    elapsed = round(time.time() - t0, 1)
    print(f"  Test accuracy={acc:.4f}  elapsed={elapsed}s")
    for cls in CLASS_NAMES:
        if cls in report:
            r = report[cls]
            print(f"  {cls}: precision={r['precision']:.3f}  recall={r['recall']:.3f}")

    result = {
        "experiment":              name,
        "id":                      exp["id"],
        "classifier":              "multiclass",
        "label_config":            label_cfg,
        "balance_mode":            balance_mode,
        "labeled_rows":            len(df),
        "label_distribution":      label_dist,
        "test_rows":               len(test),
        "test_accuracy":           round(float(acc), 4),
        "test_classification_report": report,
        **cm_data,
        "signal_analysis":         thr_results,
        "per_ticker":              per_ticker,
        "feature_importance":      fi_data,
        "elapsed_seconds":         elapsed,
    }
    return result, model


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def build_summary_row(result: dict) -> dict:
    classifier = result.get("classifier", "multiclass")
    if classifier == "binary":
        rep = result.get("binary_report", {})
        return {
            "experiment":        result["experiment"],
            "classifier":        "binary",
            "label_mode":        result["label_config"].get("mode"),
            "horizon":           result["label_config"].get("horizon_candles"),
            "SHORT_precision":   rep.get("SHORT_precision"),
            "SHORT_recall":      rep.get("SHORT_recall"),
            "SHORT_f1":          rep.get("SHORT_f1"),
            "accuracy":          rep.get("accuracy"),
        }

    rep      = result.get("test_classification_report", {})
    thr_list = result.get("signal_analysis", [])

    best_short = None
    best_prec  = -1.0
    for r in thr_list:
        p = r.get("short_precision")
        c = r.get("short_signals", 0)
        if p is not None and c >= 20 and p > best_prec:
            best_prec  = p
            best_short = {"threshold": r["threshold"], "short_precision": p, "short_signals": c}

    return {
        "experiment":       result["experiment"],
        "classifier":       "multiclass",
        "label_mode":       result["label_config"].get("mode"),
        "horizon":          result["label_config"].get("horizon_candles"),
        "balance_mode":     result.get("balance_mode"),
        "test_accuracy":    result.get("test_accuracy"),
        "DOWN_precision":   round(float(rep.get("DOWN", {}).get("precision", 0)), 4),
        "DOWN_recall":      round(float(rep.get("DOWN", {}).get("recall",    0)), 4),
        "UP_precision":     round(float(rep.get("UP",   {}).get("precision", 0)), 4),
        "UP_recall":        round(float(rep.get("UP",   {}).get("recall",    0)), 4),
        "best_short_signal_threshold": best_short,
    }


def _load_fractal_backtest_summary(reports_root: Path) -> dict:
    path = reports_root / "fractals" / "fractal_backtest_summary.json"
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def build_cross_summary(
    ml_rows: list,
    fractal_summary: dict,
    reports_dir: Path,
) -> dict:
    """Combine ML experiment results with fractal backtest results."""
    # Top fractal features by importance (from exp 1 or 2 if available)
    top_pa_features = []
    for row in ml_rows:
        if row.get("classifier") == "multiclass":
            fi = None
            # Try to load full result from disk
            exp_name = row.get("experiment", "")
            exp_file = reports_dir / f"{exp_name}.json"
            if exp_file.exists():
                with open(exp_file) as f:
                    full = json.load(f)
                fi = full.get("feature_importance", [])
            if fi:
                pa_fi = [
                    x for x in fi
                    if x["feature"] not in set(FEATURE_COLUMNS)
                ][:10]
                top_pa_features = pa_fi
                break

    # Best SHORT-focused result
    best_short_exp = None
    best_short_prec = -1.0
    for row in ml_rows:
        p = row.get("SHORT_precision") or row.get("DOWN_precision") or 0.0
        if p > best_short_prec:
            best_short_prec = p
            best_short_exp  = row.get("experiment")

    frac_strategies = fractal_summary.get("strategies", [])
    best_by_net = fractal_summary.get("best_by_cumulative_net")
    best_by_pf  = fractal_summary.get("best_by_profit_factor")

    promising = [s["strategy"] for s in frac_strategies if s.get("promising")]

    return {
        "fractal_backtest": {
            "best_by_cumulative_net_return": best_by_net,
            "best_by_profit_factor":         best_by_pf,
            "promising_strategies":          promising,
            "note": fractal_summary.get("note", ""),
        },
        "ml_experiments": {
            "best_by_short_precision":  best_short_exp,
            "best_short_precision":     round(best_short_prec, 4) if best_short_prec >= 0 else None,
            "summary_rows":             ml_rows,
        },
        "top_price_action_features_by_importance": top_pa_features,
        "research_status": "offline only — not integrated into APK",
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="CatBoost experiments with price action features")
    parser.add_argument("--config",      default=None)
    parser.add_argument("--timeframe",   default=None)
    parser.add_argument(
        "--experiments",
        nargs="+", type=int, default=None,
        help="Experiment IDs to run (default: all). E.g. --experiments 1 2",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config = _load_config(args.config)
    if args.timeframe:
        config["timeframe"] = args.timeframe

    thresholds = config.get("signal_thresholds", DEFAULT_THRESHOLDS)

    selected = EXPERIMENTS
    if args.experiments:
        ids      = set(args.experiments)
        selected = [e for e in EXPERIMENTS if e["id"] in ids]
        if not selected:
            print(f"No experiments matched IDs: {ids}")
            sys.exit(1)

    print(f"Experiments to run: {[e['name'] for e in selected]}")
    if args.dry_run:
        for e in selected:
            print(f"  [{e['id']}] {e['name']}  classifier={e['classifier']}  label={e['label']}")
        return

    reports_dir = _rpath(config["output"]["reports_dir"]) / "price_action"
    reports_dir.mkdir(parents=True, exist_ok=True)

    print("\nLoading feature dataset…")
    df_features = load_features_only(config)
    print(f"  {len(df_features):,} rows loaded")

    summary_rows = []
    for exp in selected:
        try:
            result, _ = run_experiment(exp, config, df_features, thresholds)
            out_path  = reports_dir / f"{exp['name']}.json"
            # Do not overwrite unless the file does not exist, or always write fresh results.
            with open(out_path, "w") as f:
                json.dump(result, f, indent=2)
            print(f"  Saved: {out_path}")
            summary_rows.append(build_summary_row(result))
        except Exception as exc:
            import traceback
            print(f"  [ERROR] {exp['name']}: {exc}")
            traceback.print_exc()

    if not summary_rows:
        print("No experiments completed — no summary written.")
        return

    # Cross-summary with fractal backtest (if available)
    fractal_summary = _load_fractal_backtest_summary(_rpath(config["output"]["reports_dir"]))
    cross = build_cross_summary(summary_rows, fractal_summary, reports_dir)

    summary_path = reports_dir / "summary.json"
    with open(summary_path, "w") as f:
        json.dump(cross, f, indent=2)
    print(f"\nCross-summary saved: {summary_path}")

    # Print comparison table
    print("\n--- EXPERIMENT SUMMARY ---")
    for row in summary_rows:
        print(f"\n  {row['experiment']} [{row.get('classifier')}]")
        if row.get("classifier") == "binary":
            print(f"    SHORT precision={row.get('SHORT_precision')}  "
                  f"recall={row.get('SHORT_recall')}  F1={row.get('SHORT_f1')}")
        else:
            print(f"    accuracy={row.get('test_accuracy')}  "
                  f"DOWN: P={row.get('DOWN_precision')} R={row.get('DOWN_recall')}  "
                  f"UP: P={row.get('UP_precision')} R={row.get('UP_recall')}")
            if row.get("best_short_signal_threshold"):
                bs = row["best_short_signal_threshold"]
                print(f"    best SHORT signal: thr={bs['threshold']}  "
                      f"precision={bs['short_precision']}  n={bs['short_signals']}")

    print("\nInspect first: ml/reports/price_action/summary.json")


if __name__ == "__main__":
    main()
