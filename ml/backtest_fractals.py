"""
Rule-based fractal/price-action strategies — offline backtest.

Four strategies
---------------
1. fractal_breakout_long    — bullish/neutral structure + close crosses above last fractal high
2. fractal_breakdown_short  — bearish/neutral structure + close crosses below last fractal low
3. sweep_reversal_long      — low sweeps fractal low, close recovers above
4. sweep_reversal_short     — high sweeps fractal high, close falls back below

Entry: signal bar close.
Exit:  path-based TP/SL using future high/low; horizon-close if neither hit.
No future leakage: signals use only confirmed fractal columns.

Usage (from repo root):
    python ml\\backtest_fractals.py
    python ml\\backtest_fractals.py --no-volume-filter
    python ml\\backtest_fractals.py --tickers SBER GAZP LKOH

Output:
    ml/reports/fractals/fractal_backtest_summary.json
    ml/reports/fractals/fractal_breakout_long.json
    ml/reports/fractals/fractal_breakdown_short.json
    ml/reports/fractals/sweep_reversal_long.json
    ml/reports/fractals/sweep_reversal_short.json
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

_ML_DIR   = Path(__file__).parent
_REPO_ROOT = _ML_DIR.parent

sys.path.insert(0, str(_ML_DIR))
from fractal_features import add_confirmed_fractals_grouped
from price_action import add_price_action_features_grouped

# ---------------------------------------------------------------------------
# Default configuration
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "horizon_candles":          12,
    "take_profit_pct":          0.40,
    "stop_loss_pct":            0.25,
    "commission_bps":           5,
    "slippage_bps":             5,
    "min_bars_between_signals": 3,
    "volume_filter":            True,   # require volume_zscore_20 > 0
    "fractal_left_span":        2,
    "fractal_right_span":       2,
    "allowed_tickers":          None,   # None = all tickers in dataset
}


# ---------------------------------------------------------------------------
# Signal generation (vectorised, per-ticker numpy arrays)
# ---------------------------------------------------------------------------

def _signals_fractal_breakout_long(arr: dict, cfg: dict) -> np.ndarray:
    """1 at bars where fractal breakout long signal fires."""
    n = len(arr["close"])
    bos_up   = arr["break_of_structure_up"]
    struct   = arr["structure_trend"]
    vol      = arr["volume_zscore_20"]

    sig = np.zeros(n, dtype=bool)
    for i in range(1, n):
        if np.isnan(bos_up[i]) or bos_up[i] != 1:
            continue
        if struct[i] not in (0.0, 1.0):   # bearish → skip
            continue
        if cfg["volume_filter"] and (np.isnan(vol[i]) or vol[i] <= 0):
            continue
        sig[i] = True
    return sig


def _signals_fractal_breakdown_short(arr: dict, cfg: dict) -> np.ndarray:
    n = len(arr["close"])
    bos_dn = arr["break_of_structure_down"]
    struct = arr["structure_trend"]
    vol    = arr["volume_zscore_20"]

    sig = np.zeros(n, dtype=bool)
    for i in range(1, n):
        if np.isnan(bos_dn[i]) or bos_dn[i] != 1:
            continue
        if struct[i] not in (0.0, -1.0):   # bullish → skip
            continue
        if cfg["volume_filter"] and (np.isnan(vol[i]) or vol[i] <= 0):
            continue
        sig[i] = True
    return sig


def _signals_sweep_reversal_long(arr: dict, cfg: dict) -> np.ndarray:
    n   = len(arr["close"])
    slr = arr["sweep_low_reversal"]
    sig = np.zeros(n, dtype=bool)
    for i in range(n):
        if not np.isnan(slr[i]) and slr[i] == 1:
            sig[i] = True
    return sig


def _signals_sweep_reversal_short(arr: dict, cfg: dict) -> np.ndarray:
    n   = len(arr["close"])
    shr = arr["sweep_high_reversal"]
    sig = np.zeros(n, dtype=bool)
    for i in range(n):
        if not np.isnan(shr[i]) and shr[i] == 1:
            sig[i] = True
    return sig


# ---------------------------------------------------------------------------
# Trade simulation
# ---------------------------------------------------------------------------

def _simulate_trades(
    df_ticker: pd.DataFrame,
    raw_signals: np.ndarray,
    direction: str,   # "long" or "short"
    strategy_name: str,
    cfg: dict,
) -> list:
    """
    Simulate trades for one ticker/strategy.

    Entry at signal bar close.
    Exit: TP/SL path-check on future bars; horizon close if neither hit.
    Gross return is based on raw price movement.
    Net = gross − 2*(commission_bps + slippage_bps)/100.
    """
    closes = df_ticker["close"].values
    highs  = df_ticker["high"].values
    lows   = df_ticker["low"].values
    dates  = df_ticker["datetime"].values
    ticker = df_ticker["ticker"].iloc[0]
    n      = len(closes)

    tp_pct     = cfg["take_profit_pct"]
    sl_pct     = cfg["stop_loss_pct"]
    horizon    = cfg["horizon_candles"]
    cost_pct   = (cfg["commission_bps"] + cfg["slippage_bps"]) * 2 / 100
    min_gap    = cfg["min_bars_between_signals"]

    trades = []
    last_signal_bar = -(min_gap + 1)

    for i in range(n - horizon):
        if not raw_signals[i]:
            continue
        if i - last_signal_bar < min_gap:
            continue
        if np.isnan(closes[i]) or closes[i] <= 0:
            continue

        entry = closes[i]
        last_signal_bar = i

        if direction == "long":
            tp_price = entry * (1.0 + tp_pct / 100.0)
            sl_price = entry * (1.0 - sl_pct / 100.0)
        else:
            tp_price = entry * (1.0 - tp_pct / 100.0)
            sl_price = entry * (1.0 + sl_pct / 100.0)

        exit_price = None
        exit_type  = "horizon"

        for j in range(i + 1, i + 1 + horizon):
            h = highs[j]
            lo = lows[j]

            if direction == "long":
                tp_hit = h >= tp_price
                sl_hit = lo <= sl_price
                if tp_hit and sl_hit:
                    exit_price = sl_price   # conservative: assume SL filled first
                    exit_type  = "sl"
                    break
                elif tp_hit:
                    exit_price = tp_price
                    exit_type  = "tp"
                    break
                elif sl_hit:
                    exit_price = sl_price
                    exit_type  = "sl"
                    break
            else:  # short
                tp_hit = lo <= tp_price
                sl_hit = h  >= sl_price
                if tp_hit and sl_hit:
                    exit_price = sl_price
                    exit_type  = "sl"
                    break
                elif tp_hit:
                    exit_price = tp_price
                    exit_type  = "tp"
                    break
                elif sl_hit:
                    exit_price = sl_price
                    exit_type  = "sl"
                    break

        if exit_price is None:
            exit_price = closes[min(i + horizon, n - 1)]

        if direction == "long":
            gross = (exit_price / entry - 1.0) * 100.0
        else:
            gross = (entry / exit_price - 1.0) * 100.0

        net = gross - cost_pct

        trades.append({
            "strategy":        strategy_name,
            "ticker":          ticker,
            "direction":       direction,
            "entry_bar":       int(i),
            "datetime":        str(dates[i]),
            "entry_price":     round(float(entry), 6),
            "exit_price":      round(float(exit_price), 6),
            "exit_type":       exit_type,
            "gross_return_pct": round(float(gross), 4),
            "net_return_pct":   round(float(net), 4),
        })

    return trades


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def compute_metrics(trades: list, strategy_name: str) -> dict:
    if not trades:
        return {
            "strategy":     strategy_name,
            "total_trades": 0,
            "note":         "no trades generated",
        }

    tdf = pd.DataFrame(trades)
    nr  = tdf["net_return_pct"].values
    gr  = tdf["gross_return_pct"].values

    total  = len(tdf)
    longs  = int((tdf["direction"] == "long").sum())
    shorts = int((tdf["direction"] == "short").sum())
    wins   = int((nr > 0).sum())

    pos = nr[nr > 0]
    neg = nr[nr < 0]
    pf  = (float(pos.sum()) / float(abs(neg.sum()))) if len(neg) > 0 and neg.sum() != 0 else None

    cum_ret   = np.cumsum(nr)
    roll_max  = np.maximum.accumulate(cum_ret)
    max_dd    = float((roll_max - cum_ret).max())

    # Per ticker
    per_ticker = {}
    for tk, grp in tdf.groupby("ticker"):
        r = grp["net_return_pct"].values
        w = int((r > 0).sum())
        per_ticker[str(tk)] = {
            "trades":                   int(len(r)),
            "win_rate":                 round(w / len(r), 4),
            "avg_net_return_pct":       round(float(r.mean()), 4),
            "cumulative_net_return_pct": round(float(r.sum()), 4),
        }

    # Per exit type
    per_exit = {}
    for et, grp in tdf.groupby("exit_type"):
        r = grp["net_return_pct"].values
        per_exit[str(et)] = {"count": int(len(r)), "avg_net_return_pct": round(float(r.mean()), 4)}

    # Monthly
    monthly = {}
    if "datetime" in tdf.columns:
        tdf["_month"] = pd.to_datetime(tdf["datetime"]).dt.to_period("M").astype(str)
        for mo, grp in tdf.groupby("_month"):
            r = grp["net_return_pct"].values
            w = int((r > 0).sum())
            monthly[str(mo)] = {
                "trades":          int(len(r)),
                "win_rate":        round(w / len(r), 4),
                "net_return_pct":  round(float(r.sum()), 4),
            }

    return {
        "strategy":                    strategy_name,
        "total_trades":                total,
        "long_trades":                 longs,
        "short_trades":                shorts,
        "win_rate":                    round(wins / total, 4),
        "avg_gross_return_pct":        round(float(gr.mean()), 4),
        "avg_net_return_pct":          round(float(nr.mean()), 4),
        "median_net_return_pct":       round(float(np.median(nr)), 4),
        "cumulative_net_return_pct":   round(float(nr.sum()), 4),
        "profit_factor":               round(pf, 4) if pf is not None else None,
        "max_drawdown_pct":            round(max_dd, 4),
        "best_trade_pct":              round(float(nr.max()), 4),
        "worst_trade_pct":             round(float(nr.min()), 4),
        "per_exit_type":               per_exit,
        "per_ticker":                  per_ticker,
        "monthly":                     monthly,
    }


# ---------------------------------------------------------------------------
# Strategy runner
# ---------------------------------------------------------------------------

_STRATEGIES = [
    ("fractal_breakout_long",   _signals_fractal_breakout_long,   "long"),
    ("fractal_breakdown_short", _signals_fractal_breakdown_short, "short"),
    ("sweep_reversal_long",     _signals_sweep_reversal_long,     "long"),
    ("sweep_reversal_short",    _signals_sweep_reversal_short,    "short"),
]

_SIGNAL_COLS = [
    "close", "high", "low", "datetime", "ticker",
    "break_of_structure_up", "break_of_structure_down",
    "sweep_low_reversal", "sweep_high_reversal",
    "structure_trend", "volume_zscore_20",
]


def run_backtest(df: pd.DataFrame, cfg: dict) -> dict:
    """
    Run all four strategies on the pre-feature-enriched DataFrame.
    Returns dict keyed by strategy name → metrics dict.
    """
    results = {}

    for strategy_name, signal_fn, direction in _STRATEGIES:
        print(f"  Running strategy: {strategy_name}")
        all_trades = []

        for ticker, grp in df.groupby("ticker", sort=False):
            grp = grp.sort_values("datetime").reset_index(drop=True)
            arr = {col: grp[col].values for col in _SIGNAL_COLS if col in grp.columns}
            sigs = signal_fn(arr, cfg)
            trades = _simulate_trades(grp, sigs, direction, strategy_name, cfg)
            all_trades.extend(trades)

        metrics = compute_metrics(all_trades, strategy_name)
        results[strategy_name] = metrics
        n_trades = metrics.get("total_trades", 0)
        if n_trades > 0:
            wr  = metrics.get("win_rate", 0)
            pf  = metrics.get("profit_factor", "N/A")
            cum = metrics.get("cumulative_net_return_pct", 0)
            print(f"    trades={n_trades}  win_rate={wr:.2%}  PF={pf}  cum_net={cum:.2f}%")
        else:
            print("    no trades")

    return results


# ---------------------------------------------------------------------------
# Load helpers
# ---------------------------------------------------------------------------

def _load_config(config_path=None) -> dict:
    if config_path is None:
        config_path = _ML_DIR / "config" / "default.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def _load_dataset(config: dict) -> pd.DataFrame:
    tf          = config["timeframe"]
    proc_dir    = _REPO_ROOT / config["output"]["processed_dir"]
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Rule-based fractal backtest")
    parser.add_argument("--config",           default=None)
    parser.add_argument("--horizon",          type=int,   default=None)
    parser.add_argument("--tp",               type=float, default=None, dest="take_profit_pct")
    parser.add_argument("--sl",               type=float, default=None, dest="stop_loss_pct")
    parser.add_argument("--no-volume-filter", action="store_true")
    parser.add_argument("--tickers",          nargs="+",  default=None)
    parser.add_argument("--left-span",        type=int,   default=2)
    parser.add_argument("--right-span",       type=int,   default=2)
    args = parser.parse_args()

    base_config = _load_config(args.config)

    cfg = dict(DEFAULT_CONFIG)
    if args.horizon:
        cfg["horizon_candles"] = args.horizon
    if args.take_profit_pct:
        cfg["take_profit_pct"] = args.take_profit_pct
    if args.stop_loss_pct:
        cfg["stop_loss_pct"] = args.stop_loss_pct
    if args.no_volume_filter:
        cfg["volume_filter"] = False
    if args.tickers:
        cfg["allowed_tickers"] = args.tickers
    cfg["fractal_left_span"]  = args.left_span
    cfg["fractal_right_span"] = args.right_span

    print("Loading dataset…")
    df = _load_dataset(base_config)

    if cfg["allowed_tickers"]:
        df = df[df["ticker"].isin(cfg["allowed_tickers"])].reset_index(drop=True)
        print(f"  Filtered to tickers: {cfg['allowed_tickers']}")

    print(f"  {len(df):,} rows across {df['ticker'].nunique()} tickers")

    print("Adding fractal features…")
    df = add_confirmed_fractals_grouped(
        df,
        left_span=cfg["fractal_left_span"],
        right_span=cfg["fractal_right_span"],
    )

    print("Adding price action features…")
    df = add_price_action_features_grouped(df)

    print("\nRunning strategies…")
    print(f"  Config: horizon={cfg['horizon_candles']}  TP={cfg['take_profit_pct']}%  "
          f"SL={cfg['stop_loss_pct']}%  vol_filter={cfg['volume_filter']}")
    results = run_backtest(df, cfg)

    # --- Save reports ---
    reports_dir = _REPO_ROOT / base_config["output"]["reports_dir"] / "fractals"
    reports_dir.mkdir(parents=True, exist_ok=True)

    per_strategy_files = {
        "fractal_breakout_long":   "fractal_breakout_long.json",
        "fractal_breakdown_short": "fractal_breakdown_short.json",
        "sweep_reversal_long":     "sweep_reversal_long.json",
        "sweep_reversal_short":    "sweep_reversal_short.json",
    }

    for strategy_name, filename in per_strategy_files.items():
        path = reports_dir / filename
        with open(path, "w") as f:
            json.dump(results.get(strategy_name, {}), f, indent=2)
        print(f"  Saved: {path}")

    # Summary
    def _is_promising(m: dict) -> bool:
        return (
            m.get("total_trades", 0) >= 200
            and len(m.get("per_ticker", {})) >= 3
        )

    summary_rows = []
    for name, m in results.items():
        summary_rows.append({
            "strategy":                  name,
            "total_trades":              m.get("total_trades", 0),
            "win_rate":                  m.get("win_rate"),
            "avg_net_return_pct":        m.get("avg_net_return_pct"),
            "median_net_return_pct":     m.get("median_net_return_pct"),
            "cumulative_net_return_pct": m.get("cumulative_net_return_pct"),
            "profit_factor":             m.get("profit_factor"),
            "max_drawdown_pct":          m.get("max_drawdown_pct"),
            "promising":                 _is_promising(m),
        })

    best_by_net = max(
        (r for r in summary_rows if r["total_trades"] >= 200),
        key=lambda x: x.get("cumulative_net_return_pct") or -1e9,
        default=None,
    )
    best_by_pf = max(
        (r for r in summary_rows if r["total_trades"] >= 200 and r.get("profit_factor")),
        key=lambda x: x.get("profit_factor") or 0,
        default=None,
    )

    summary = {
        "config":                   cfg,
        "strategies":               summary_rows,
        "best_by_cumulative_net":   best_by_net["strategy"] if best_by_net else None,
        "best_by_profit_factor":    best_by_pf["strategy"]  if best_by_pf  else None,
        "note": (
            "A strategy is promising only if total_trades >= 200 "
            "and trades span >= 3 tickers."
        ),
    }

    summary_path = reports_dir / "fractal_backtest_summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\nSummary saved: {summary_path}")

    # Print table
    print("\n--- STRATEGY SUMMARY ---")
    header = f"{'Strategy':<28}  {'Trades':>6}  {'WinRate':>7}  {'CumNet%':>8}  {'PF':>5}  {'Promising'}"
    print(header)
    for r in summary_rows:
        pf  = r.get("profit_factor")
        pf_s = f"{pf:.2f}" if pf else "  N/A"
        cn  = r.get("cumulative_net_return_pct")
        cn_s = f"{cn:+.2f}" if cn is not None else "   N/A"
        wr  = r.get("win_rate")
        wr_s = f"{wr:.1%}" if wr is not None else "  N/A"
        flag = "YES" if r["promising"] else "no"
        print(f"{r['strategy']:<28}  {r['total_trades']:>6}  {wr_s:>7}  {cn_s:>8}  {pf_s:>5}  {flag}")

    print("\nInspect first: ml/reports/fractals/fractal_backtest_summary.json")


if __name__ == "__main__":
    main()
