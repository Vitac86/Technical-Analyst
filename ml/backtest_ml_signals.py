"""
Offline backtest for ML-generated SHORT/LONG signals.

Reproduces the exact feature pipeline from experiments_price_action.py,
loads trained CatBoost model binaries, generates signals on the test split,
and simulates trades with TP/SL/horizon exit.

Models backtested
-----------------
1. catboost_pa_tp_sl_h12_tp040_sl025_balanced  (multiclass: DOWN/FLAT/UP)
2. catboost_pa_short_focused_tp_sl_h12          (binary: SHORT_SETUP vs NOT_SHORT)

Signal rules
------------
Multiclass:
  SHORT    if P(DOWN) >= threshold and P(DOWN) > P(UP)
  LONG     if P(UP)   >= threshold and P(UP)   > P(DOWN)
  NO_TRADE otherwise

Binary short:
  SHORT    if P(SHORT_SETUP=1) >= threshold
  NO_TRADE otherwise

Backtest assumptions
--------------------
- Entry at signal candle close.
- SHORT exit: TP when future low  <= entry*(1 - tp_pct/100);
              SL when future high >= entry*(1 + sl_pct/100).
- LONG  exit: TP when future high >= entry*(1 + tp_pct/100);
              SL when future low  <= entry*(1 - sl_pct/100).
- Same candle both TP and SL hit → SL wins (conservative).
- Neither hit after horizon_candles → exit at horizon-bar close.
- Round-trip cost: 2*(commission_bps + slippage_bps)/100 pct.

Usage (from repo root)
----------------------
python ml\\backtest_ml_signals.py --all
python ml\\backtest_ml_signals.py --model catboost_pa_tp_sl_h12_tp040_sl025_balanced
python ml\\backtest_ml_signals.py --model catboost_pa_short_focused_tp_sl_h12

Output
------
ml/reports/ml_signal_backtests/<model_name>_backtest.json
ml/reports/ml_signal_backtests/summary.json
"""
import argparse
import json
import sys
import traceback
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from catboost import CatBoostClassifier

_ML_DIR    = Path(__file__).parent
_REPO_ROOT = _ML_DIR.parent
sys.path.insert(0, str(_ML_DIR))

from experiments_price_action import (
    add_pa_features,
    apply_labels,
    drop_invalid_pa_rows,
    load_features_only,
)
from features import FEATURE_COLUMNS
from labels import CLASS_TO_INT
from price_action import PRICE_ACTION_FEATURE_COLUMNS
from train_catboost import time_split


# ---------------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------------

MODELS = [
    {
        "name":  "catboost_pa_tp_sl_h12_tp040_sl025_balanced",
        "type":  "multiclass",
        "label": {
            "mode":            "tp_sl",
            "horizon_candles": 12,
            "take_profit_pct": 0.40,
            "stop_loss_pct":   0.25,
        },
    },
    {
        "name":  "catboost_pa_short_focused_tp_sl_h12",
        "type":  "binary_short",
        "label": {
            "mode":            "tp_sl",
            "horizon_candles": 12,
            "take_profit_pct": 0.40,
            "stop_loss_pct":   0.25,
        },
    },
]

THRESHOLDS = [0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75]

DEFAULT_BACKTEST_CFG = {
    "horizon_candles": 12,
    "take_profit_pct": 0.40,
    "stop_loss_pct":   0.25,
    "commission_bps":  5,
    "slippage_bps":    5,
}


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _load_config(config_path=None) -> dict:
    if config_path is None:
        config_path = _ML_DIR / "config" / "default.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def _rpath(rel: str, base: Path = _REPO_ROOT) -> Path:
    p = Path(rel)
    return p if p.is_absolute() else (base / p).resolve()


# ---------------------------------------------------------------------------
# Feature pipeline — mirrors experiments_price_action.py exactly
# ---------------------------------------------------------------------------

def build_test_split(config: dict, label_cfg: dict):
    """
    Reproduce the full feature pipeline and return the test split.

    Returns (test_df, all_feature_cols, test_period_info)
    """
    print("  Loading dataset…")
    df_features = load_features_only(config)

    if label_cfg["mode"] == "tp_sl":
        print("  [tp_sl] Building path-based labels — may take several minutes…")
    df = apply_labels(df_features, label_cfg)

    print("  Adding fractal + price action features…")
    df = add_pa_features(df)

    all_feature_cols = FEATURE_COLUMNS + [
        c for c in PRICE_ACTION_FEATURE_COLUMNS if c not in FEATURE_COLUMNS
    ]
    df = drop_invalid_pa_rows(df, all_feature_cols)

    train_cfg = config["train"]
    _, _, test = time_split(df, train_cfg["train_frac"], train_cfg["val_frac"])
    test = test.reset_index(drop=True)

    period = {
        "from": str(test["datetime"].min()),
        "to":   str(test["datetime"].max()),
    }
    print(f"  Test rows: {len(test):,}  tickers: {test['ticker'].nunique()}  "
          f"period: {period['from'][:10]} → {period['to'][:10]}")

    return test, all_feature_cols, period


# ---------------------------------------------------------------------------
# Signal generation
# ---------------------------------------------------------------------------

def generate_signals(
    probas: np.ndarray,
    threshold: float,
    model_type: str,
    down_idx: int = 0,
    up_idx: int = 2,
    short_idx: int = 1,
) -> np.ndarray:
    """Return string array: 'SHORT', 'LONG', or 'NO_TRADE' for each row."""
    n       = len(probas)
    signals = np.full(n, "NO_TRADE", dtype=object)

    if model_type == "multiclass":
        p_down = probas[:, down_idx]
        p_up   = probas[:, up_idx]
        signals[(p_down >= threshold) & (p_down > p_up)] = "SHORT"
        signals[(p_up   >= threshold) & (p_up   > p_down)] = "LONG"
    elif model_type == "binary_short":
        signals[probas[:, short_idx] >= threshold] = "SHORT"

    return signals


# ---------------------------------------------------------------------------
# Trade simulation
# ---------------------------------------------------------------------------

def _simulate_trades_per_ticker(
    grp: pd.DataFrame,
    ticker_signals: np.ndarray,
    direction: str,
    cfg: dict,
) -> list:
    """
    Simulate TP/SL/horizon trades for one ticker.

    ticker_signals — bool array aligned with grp (after sort_values+reset_index).
    """
    closes = grp["close"].values
    highs  = grp["high"].values
    lows   = grp["low"].values
    dates  = grp["datetime"].values
    ticker = grp["ticker"].iloc[0]
    n      = len(closes)

    tp_pct   = cfg["take_profit_pct"]
    sl_pct   = cfg["stop_loss_pct"]
    horizon  = cfg["horizon_candles"]
    cost_pct = (cfg["commission_bps"] + cfg["slippage_bps"]) * 2 / 100

    trades = []

    # Only consider bars where a full horizon of future bars is available
    for i in range(n - horizon):
        if not ticker_signals[i]:
            continue
        if np.isnan(closes[i]) or closes[i] <= 0:
            continue

        entry = closes[i]

        if direction == "short":
            tp_price = entry * (1.0 - tp_pct / 100.0)
            sl_price = entry * (1.0 + sl_pct / 100.0)
        else:
            tp_price = entry * (1.0 + tp_pct / 100.0)
            sl_price = entry * (1.0 - sl_pct / 100.0)

        exit_price   = None
        exit_type    = "horizon"
        exit_bar_idx = i + horizon

        for j in range(i + 1, i + 1 + horizon):
            h  = highs[j]
            lo = lows[j]

            if direction == "short":
                tp_hit = lo <= tp_price
                sl_hit = h  >= sl_price
            else:
                tp_hit = h  >= tp_price
                sl_hit = lo <= sl_price

            if tp_hit and sl_hit:
                exit_price, exit_type, exit_bar_idx = sl_price, "sl", j
                break
            elif tp_hit:
                exit_price, exit_type, exit_bar_idx = tp_price, "tp", j
                break
            elif sl_hit:
                exit_price, exit_type, exit_bar_idx = sl_price, "sl", j
                break

        if exit_price is None:
            exit_price = closes[exit_bar_idx]

        if direction == "short":
            gross = (entry / exit_price - 1.0) * 100.0
        else:
            gross = (exit_price / entry - 1.0) * 100.0

        trades.append({
            "ticker":           ticker,
            "direction":        direction,
            "entry_bar":        int(i),
            "exit_bar":         int(exit_bar_idx),
            "holding_candles":  int(exit_bar_idx - i),
            "datetime":         str(dates[i]),
            "entry_price":      round(float(entry), 6),
            "exit_price":       round(float(exit_price), 6),
            "exit_type":        exit_type,
            "gross_return_pct": round(float(gross), 4),
            "net_return_pct":   round(float(gross - cost_pct), 4),
        })

    return trades


def simulate_all_trades(
    test_df: pd.DataFrame,
    signals: np.ndarray,
    cfg: dict,
) -> list:
    """Simulate trades across all tickers for the given signal array."""
    # signals is positionally aligned with test_df (both reset_index'd to 0-based)
    df = test_df.copy()
    df["_signal"] = signals  # positional assignment since signals is ndarray

    all_trades = []
    for _, grp in df.groupby("ticker", sort=False):
        grp       = grp.sort_values("datetime").reset_index(drop=True)
        short_arr = (grp["_signal"] == "SHORT").values
        long_arr  = (grp["_signal"] == "LONG").values
        all_trades.extend(_simulate_trades_per_ticker(grp, short_arr, "short", cfg))
        all_trades.extend(_simulate_trades_per_ticker(grp, long_arr,  "long",  cfg))

    return all_trades


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _side_metrics(side_df: pd.DataFrame) -> dict:
    """Summarise one direction (long or short) of trades."""
    if len(side_df) == 0:
        return {"trades": 0}
    nr  = side_df["net_return_pct"].values
    gr  = side_df["gross_return_pct"].values
    pos = nr[nr > 0]
    neg = nr[nr < 0]
    pf  = (float(pos.sum()) / float(abs(neg.sum()))) if len(neg) > 0 and neg.sum() != 0 else None
    wins = int((nr > 0).sum())
    return {
        "trades":                    int(len(side_df)),
        "win_rate":                  round(wins / len(side_df), 4),
        "avg_gross_return_pct":      round(float(gr.mean()), 4),
        "avg_net_return_pct":        round(float(nr.mean()), 4),
        "median_net_return_pct":     round(float(np.median(nr)), 4),
        "cumulative_net_return_pct": round(float(nr.sum()), 4),
        "profit_factor":             round(pf, 4) if pf is not None else None,
        "best_trade_pct":            round(float(nr.max()), 4),
        "worst_trade_pct":           round(float(nr.min()), 4),
    }


def compute_backtest_metrics(
    trades: list,
    threshold: float,
    model_name: str,
) -> dict:
    base = {"model": model_name, "threshold": threshold}

    if not trades:
        base.update({
            "total_signals": 0, "long_signals": 0, "short_signals": 0,
            "win_rate": None, "avg_gross_return_pct": None,
            "avg_net_return_pct": None, "median_net_return_pct": None,
            "cumulative_net_return_pct": None, "profit_factor": None,
            "max_drawdown_pct": None, "best_trade_pct": None,
            "worst_trade_pct": None, "avg_holding_candles": None,
            "take_profit_count": 0, "stopped_out_count": 0,
            "horizon_exit_count": 0, "per_ticker": {}, "monthly": {},
            "long_only": {"trades": 0}, "short_only": {"trades": 0},
            "ticker_concentration_max_pct": None, "n_tickers_with_trades": 0,
            "note": "no trades generated",
        })
        return base

    tdf  = pd.DataFrame(trades)
    nr   = tdf["net_return_pct"].values
    gr   = tdf["gross_return_pct"].values
    total        = len(tdf)
    long_trades  = int((tdf["direction"] == "long").sum())
    short_trades = int((tdf["direction"] == "short").sum())
    wins         = int((nr > 0).sum())

    pos = nr[nr > 0]
    neg = nr[nr < 0]
    pf  = (float(pos.sum()) / float(abs(neg.sum()))) if len(neg) > 0 and neg.sum() != 0 else None

    cum_ret  = np.cumsum(nr)
    roll_max = np.maximum.accumulate(cum_ret)
    max_dd   = float((roll_max - cum_ret).max())

    avg_holding = float(tdf["holding_candles"].mean()) if "holding_candles" in tdf.columns else None

    # Per ticker
    per_ticker: dict = {}
    for tk, grp in tdf.groupby("ticker"):
        r   = grp["net_return_pct"].values
        w   = int((r > 0).sum())
        pt  = r[r > 0]
        nt  = r[r < 0]
        pft = (float(pt.sum()) / float(abs(nt.sum()))) if len(nt) > 0 and nt.sum() != 0 else None
        per_ticker[str(tk)] = {
            "trades":                    int(len(r)),
            "win_rate":                  round(w / len(r), 4),
            "avg_net_return_pct":        round(float(r.mean()), 4),
            "cumulative_net_return_pct": round(float(r.sum()), 4),
            "profit_factor":             round(pft, 4) if pft is not None else None,
        }

    # Monthly
    monthly: dict = {}
    if "datetime" in tdf.columns:
        tdf["_month"] = pd.to_datetime(tdf["datetime"]).dt.to_period("M").astype(str)
        for mo, grp in tdf.groupby("_month"):
            r = grp["net_return_pct"].values
            w = int((r > 0).sum())
            monthly[str(mo)] = {
                "trades":                    int(len(r)),
                "win_rate":                  round(w / len(r), 4),
                "cumulative_net_return_pct": round(float(r.sum()), 4),
            }

    max_ticker_pct = max(v["trades"] / total for v in per_ticker.values()) if per_ticker else 1.0
    n_tickers      = len(per_ticker)

    base.update({
        "total_signals":              total,
        "long_signals":               long_trades,
        "short_signals":              short_trades,
        "win_rate":                   round(wins / total, 4),
        "avg_gross_return_pct":       round(float(gr.mean()), 4),
        "avg_net_return_pct":         round(float(nr.mean()), 4),
        "median_net_return_pct":      round(float(np.median(nr)), 4),
        "cumulative_net_return_pct":  round(float(nr.sum()), 4),
        "profit_factor":              round(pf, 4) if pf is not None else None,
        "max_drawdown_pct":           round(max_dd, 4),
        "best_trade_pct":             round(float(nr.max()), 4),
        "worst_trade_pct":            round(float(nr.min()), 4),
        "avg_holding_candles":        round(avg_holding, 2) if avg_holding is not None else None,
        "take_profit_count":          int((tdf["exit_type"] == "tp").sum()),
        "stopped_out_count":          int((tdf["exit_type"] == "sl").sum()),
        "horizon_exit_count":         int((tdf["exit_type"] == "horizon").sum()),
        "per_ticker":                 per_ticker,
        "monthly":                    monthly,
        "long_only":                  _side_metrics(tdf[tdf["direction"] == "long"]),
        "short_only":                 _side_metrics(tdf[tdf["direction"] == "short"]),
        "ticker_concentration_max_pct": round(max_ticker_pct, 4),
        "n_tickers_with_trades":      n_tickers,
    })
    return base


# ---------------------------------------------------------------------------
# Promising criteria
# ---------------------------------------------------------------------------

def evaluate_promising(metrics: dict) -> tuple:
    """
    Return (is_promising: bool, rejection_reasons: list[str]).

    All criteria must hold:
      - total trades    >= 200
      - SHORT trades    >= 200  (primary focus is SHORT side)
      - profit_factor   > 1.05
      - avg_net_return  > 0
      - cum_net_return  > 0
      - ticker concentration <= 50%
      - tickers with trades  >= 3
    """
    reasons = []

    total = metrics.get("total_signals", 0)
    if total < 200:
        reasons.append(f"insufficient_total_trades ({total} < 200)")

    short_trades = metrics.get("short_signals", 0)
    if short_trades < 200:
        reasons.append(f"insufficient_short_trades ({short_trades} < 200)")

    pf = metrics.get("profit_factor")
    if pf is None or pf <= 1.05:
        pf_str = f"{pf:.4f}" if pf is not None else "None"
        reasons.append(f"profit_factor_not_above_1.05 (pf={pf_str})")

    avg_net = metrics.get("avg_net_return_pct") or 0.0
    if avg_net <= 0:
        reasons.append(f"avg_net_return_not_positive ({avg_net:.4f})")

    cum_net = metrics.get("cumulative_net_return_pct") or 0.0
    if cum_net <= 0:
        reasons.append(f"cumulative_net_return_not_positive ({cum_net:.4f})")

    max_pct = metrics.get("ticker_concentration_max_pct")
    if max_pct is not None and max_pct > 0.50:
        reasons.append(f"excessive_ticker_concentration ({max_pct:.1%} > 50%)")

    n_tickers = metrics.get("n_tickers_with_trades", 0)
    if n_tickers < 3:
        reasons.append(f"insufficient_ticker_diversity ({n_tickers} < 3)")

    return len(reasons) == 0, reasons


# ---------------------------------------------------------------------------
# Per-model runner
# ---------------------------------------------------------------------------

def run_model_backtest(
    model_spec: dict,
    config: dict,
    models_dir: Path,
    thresholds: list,
    backtest_cfg: dict,
) -> dict:
    name       = model_spec["name"]
    model_type = model_spec["type"]
    label_cfg  = model_spec["label"]

    print(f"\n{'='*64}")
    print(f"Backtesting: {name}  [{model_type}]")

    model_path = models_dir / f"{name}.cbm"
    if not model_path.exists():
        msg = "Model file not found. Run python ml\\experiments_price_action.py first."
        print(f"  ERROR: {msg}")
        print(f"  Expected: {model_path}")
        return {
            "model":      name,
            "model_type": model_type,
            "status":     "error",
            "error":      msg,
            "model_path": str(model_path),
        }

    print("  Loading model…")
    model = CatBoostClassifier()
    model.load_model(str(model_path))

    # Verify class ordering so probas columns map correctly
    classes = list(model.classes_)
    if model_type == "multiclass":
        if classes != [0, 1, 2]:
            print(f"  WARNING: unexpected class order {classes}, expected [0, 1, 2]")
        down_idx  = classes.index(CLASS_TO_INT["DOWN"])   # 0
        up_idx    = classes.index(CLASS_TO_INT["UP"])     # 2
        short_idx = 0  # not used for multiclass
    else:  # binary_short
        if classes != [0, 1]:
            print(f"  WARNING: unexpected class order {classes}, expected [0, 1]")
        down_idx  = 0  # not used
        up_idx    = 0  # not used
        short_idx = classes.index(1)  # class 1 = SHORT_SETUP

    print("  Building feature pipeline…")
    test, all_feature_cols, period = build_test_split(config, label_cfg)

    X_test = test[all_feature_cols].values
    print(f"  Running predict_proba on {len(X_test):,} rows…")
    probas = model.predict_proba(X_test)

    if model_type == "multiclass" and probas.shape[1] < 3:
        return {
            "model":  name, "status": "error",
            "error":  f"Expected ≥3 classes, got {probas.shape[1]}",
        }
    if model_type == "binary_short" and probas.shape[1] < 2:
        return {
            "model":  name, "status": "error",
            "error":  f"Expected ≥2 classes, got {probas.shape[1]}",
        }

    results_by_threshold = []

    for thr in thresholds:
        signals = generate_signals(
            probas, thr, model_type,
            down_idx=down_idx, up_idx=up_idx, short_idx=short_idx,
        )

        n_short = int((signals == "SHORT").sum())
        n_long  = int((signals == "LONG").sum())
        print(f"  thr={thr:.2f}: SHORT={n_short}  LONG={n_long}  total={n_short + n_long}")

        trades  = simulate_all_trades(test, signals, backtest_cfg)
        metrics = compute_backtest_metrics(trades, thr, name)

        is_promising, reasons = evaluate_promising(metrics)
        metrics["promising"]         = is_promising
        metrics["rejection_reasons"] = reasons

        if trades:
            pf  = metrics.get("profit_factor", "N/A")
            cum = metrics.get("cumulative_net_return_pct", 0)
            wr  = metrics.get("win_rate", 0)
            flag = "PROMISING" if is_promising else f"not promising ({len(reasons)} reasons)"
            print(f"    trades={len(trades)}  win_rate={wr:.1%}  "
                  f"PF={pf}  cum_net={cum:.2f}%  [{flag}]")

        results_by_threshold.append(metrics)

    return {
        "model":              name,
        "model_type":         model_type,
        "status":             "completed",
        "label_config":       label_cfg,
        "backtest_config":    backtest_cfg,
        "test_rows":          int(len(test)),
        "test_period":        period,
        "n_test_tickers":     int(test["ticker"].nunique()),
        "threshold_results":  results_by_threshold,
    }


# ---------------------------------------------------------------------------
# Summary builder
# ---------------------------------------------------------------------------

def build_summary(model_results: list, backtest_cfg: dict) -> dict:
    all_setups: list  = []
    promising_setups  = []
    rejection_reasons = {}
    failed_models     = []

    for result in model_results:
        if result.get("status") == "error":
            failed_models.append({
                "model": result.get("model"),
                "error": result.get("error"),
            })
            continue

        for thr_res in result.get("threshold_results", []):
            setup_key = f"{result['model']}@thr={thr_res['threshold']}"
            entry = {
                "model":                     result["model"],
                "model_type":                result["model_type"],
                "threshold":                 thr_res["threshold"],
                "total_signals":             thr_res.get("total_signals", 0),
                "short_signals":             thr_res.get("short_signals", 0),
                "long_signals":              thr_res.get("long_signals", 0),
                "win_rate":                  thr_res.get("win_rate"),
                "avg_net_return_pct":        thr_res.get("avg_net_return_pct"),
                "cumulative_net_return_pct": thr_res.get("cumulative_net_return_pct"),
                "profit_factor":             thr_res.get("profit_factor"),
                "max_drawdown_pct":          thr_res.get("max_drawdown_pct"),
                "n_tickers_with_trades":     thr_res.get("n_tickers_with_trades"),
                "promising":                 thr_res.get("promising", False),
            }
            all_setups.append(entry)
            if thr_res.get("promising", False):
                promising_setups.append(entry)
            else:
                rejection_reasons[setup_key] = thr_res.get("rejection_reasons", [])

    eligible = [s for s in all_setups if (s.get("total_signals") or 0) >= 200]

    best_by_pf = max(
        (s for s in eligible if s.get("profit_factor") is not None),
        key=lambda x: x.get("profit_factor") or 0,
        default=None,
    )
    best_by_cum = max(
        eligible,
        key=lambda x: x.get("cumulative_net_return_pct") or -1e9,
        default=None,
    )
    short_eligible = [s for s in eligible if (s.get("short_signals") or 0) >= 200]
    best_short = max(
        (s for s in short_eligible if s.get("profit_factor") is not None),
        key=lambda x: x.get("profit_factor") or 0,
        default=None,
    )

    any_promising = len(promising_setups) > 0

    if any_promising:
        conclusion = (
            f"{len(promising_setups)} ML signal setup(s) passed all promising criteria. "
            "Further out-of-sample validation is strongly recommended before any live use. "
            "APK integration remains on hold until stability is confirmed."
        )
    else:
        conclusion = (
            "No ML signal setup passed all promising criteria after accounting for costs. "
            "Raw fractal rules also failed in prior testing. "
            "ML price-action features show promise in classification metrics "
            "(DOWN precision ~0.55, binary SHORT F1 ~0.63) but do not yet translate "
            "to positive net expectancy at the thresholds tested. "
            "Consider: longer horizons, tighter stop-loss, different label thresholds, "
            "or ensemble with additional features. APK integration on hold."
        )

    return {
        "any_promising":                 any_promising,
        "overall_conclusion":            conclusion,
        "best_by_profit_factor":         best_by_pf,
        "best_by_cumulative_net_return": best_by_cum,
        "best_short_setup":              best_short,
        "all_promising_setups":          promising_setups,
        "rejection_reasons_by_setup":    rejection_reasons,
        "failed_models":                 failed_models,
        "backtest_config":               backtest_cfg,
        "promising_criteria": {
            "total_trades_min":                    200,
            "short_trades_min":                    200,
            "profit_factor_min":                   1.05,
            "avg_net_return_pct_must_be_positive": True,
            "cumulative_net_return_pct_positive":  True,
            "ticker_concentration_max_pct":        0.50,
            "min_tickers_with_trades":             3,
        },
        "cost_model": {
            "commission_bps":  backtest_cfg["commission_bps"],
            "slippage_bps":    backtest_cfg["slippage_bps"],
            "roundtrip_cost_pct": (
                (backtest_cfg["commission_bps"] + backtest_cfg["slippage_bps"]) * 2 / 100
            ),
        },
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Offline backtest for ML-generated SHORT/LONG signals"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--all", action="store_true",
        help="Backtest all registered models",
    )
    group.add_argument(
        "--model", type=str,
        help="Backtest a single model by name",
    )
    parser.add_argument("--config", default=None)
    args = parser.parse_args()

    config     = _load_config(args.config)
    models_dir = _rpath(config["output"]["models_dir"])

    reports_dir = _rpath(config["output"]["reports_dir"]) / "ml_signal_backtests"
    reports_dir.mkdir(parents=True, exist_ok=True)

    if args.all:
        selected = MODELS
    else:
        selected = [m for m in MODELS if m["name"] == args.model]
        if not selected:
            known = [m["name"] for m in MODELS]
            print(f"Unknown model: {args.model}")
            print(f"Available: {known}")
            sys.exit(1)

    print(f"Models to backtest: {[m['name'] for m in selected]}")

    model_results = []
    for model_spec in selected:
        try:
            result = run_model_backtest(
                model_spec, config, models_dir, THRESHOLDS, DEFAULT_BACKTEST_CFG
            )
        except Exception as exc:
            print(f"\n[ERROR] {model_spec['name']}: {exc}")
            traceback.print_exc()
            result = {
                "model":  model_spec["name"],
                "status": "error",
                "error":  str(exc),
            }

        model_results.append(result)

        if result.get("status") == "completed":
            out_path = reports_dir / f"{model_spec['name']}_backtest.json"
            with open(out_path, "w") as f:
                json.dump(result, f, indent=2)
            print(f"  Report saved: {out_path}")

    summary      = build_summary(model_results, DEFAULT_BACKTEST_CFG)
    summary_path = reports_dir / "summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\nSummary saved: {summary_path}")

    # --- Console summary ---
    print("\n--- ML SIGNAL BACKTEST SUMMARY ---")
    print(f"\nConclusion: {summary['overall_conclusion']}")

    if summary["best_by_profit_factor"]:
        b = summary["best_by_profit_factor"]
        print(f"\nBest by profit factor: {b['model']} @ thr={b['threshold']}")
        print(f"  total={b['total_signals']}  short={b['short_signals']}  "
              f"PF={b['profit_factor']}  cum_net={b.get('cumulative_net_return_pct', 'N/A'):.2f}%")

    if summary["best_short_setup"]:
        b = summary["best_short_setup"]
        print(f"\nBest SHORT setup: {b['model']} @ thr={b['threshold']}")
        print(f"  short_trades={b['short_signals']}  PF={b['profit_factor']}  "
              f"cum_net={b.get('cumulative_net_return_pct', 'N/A'):.2f}%")

    if summary["all_promising_setups"]:
        print(f"\nPromising setups ({len(summary['all_promising_setups'])}):")
        for s in summary["all_promising_setups"]:
            print(f"  {s['model']} @ thr={s['threshold']} — "
                  f"total={s['total_signals']}  short={s['short_signals']}  "
                  f"PF={s['profit_factor']}  cum={s.get('cumulative_net_return_pct', 0):.2f}%")
    else:
        print("\nNo setups passed all promising criteria.")

    if summary["failed_models"]:
        print(f"\nFailed models ({len(summary['failed_models'])}):")
        for fm in summary["failed_models"]:
            print(f"  {fm['model']}: {fm['error']}")

    print("\nInspect: ml/reports/ml_signal_backtests/summary.json")


if __name__ == "__main__":
    main()
