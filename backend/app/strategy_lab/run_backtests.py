"""Standalone runner for the Strategy Lab backtesting core.

Loads an MT5 CSV (XAUUSDrfd H1 by default), runs a small parameter grid for
each v1 strategy, writes trades and summary CSVs, and prints leaderboards.

Run it directly::

    python backend/app/strategy_lab/run_backtests.py

or as a module from the ``backend`` directory::

    python -m app.strategy_lab.run_backtests
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

import pandas as pd

# Allow execution both as a script and as a package module.
try:
    from . import backtester, data_loader, metrics, strategies
except ImportError:  # running as a plain script
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import backtester  # type: ignore[no-redef]
    import data_loader  # type: ignore[no-redef]
    import metrics  # type: ignore[no-redef]
    import strategies  # type: ignore[no-redef]

from backtester import BacktestConfig  # noqa: E402  (resolved above)

# Repo root: run_backtests.py -> strategy_lab -> app -> backend -> ROOT
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATA_FILE = REPO_ROOT / "MetaTrader_Data" / "mt5_exports" / "XAUUSDrfd_H1.csv"
REPORTS_DIR = REPO_ROOT / "MetaTrader_Data" / "reports" / "backtests"


# ---------------------------------------------------------------------------
# Parameter grids
# ---------------------------------------------------------------------------

def _grid(**axes: list) -> list[dict]:
    """Cartesian product of named parameter axes -> list of param dicts."""
    keys = list(axes.keys())
    return [dict(zip(keys, combo)) for combo in itertools.product(*axes.values())]


def build_strategy_runs() -> list[tuple[str, dict]]:
    """Return ``(strategy_name, params)`` pairs for the whole sweep."""
    runs: list[tuple[str, dict]] = []

    for params in _grid(atr_period=[10, 14], multiplier=[2.0, 3.0, 4.0]):
        runs.append(("supertrend", params))

    for params in _grid(fast_period=[20, 50], slow_period=[100, 200]):
        runs.append(("ema_crossover", params))

    for params in _grid(lookback=[20, 55]):
        runs.append(("donchian_breakout", params))

    for params in _grid(period=[14], oversold=[25, 30], overbought=[70, 75]):
        runs.append(("rsi_mean_reversion", params))

    return runs


_SIGNAL_FUNCS = {
    "supertrend": strategies.supertrend_strategy,
    "ema_crossover": strategies.ema_crossover_strategy,
    "donchian_breakout": strategies.donchian_breakout_strategy,
    "rsi_mean_reversion": strategies.rsi_mean_reversion_strategy,
}


def _config_id(strategy_name: str, params: dict) -> str:
    """Stable identifier for a strategy + parameter set."""
    param_str = "_".join(f"{k}={v:g}" if isinstance(v, float) else f"{k}={v}"
                         for k, v in params.items())
    return f"{strategy_name}[{param_str}]"


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------

def run_sweep(
    df: pd.DataFrame,
    config: BacktestConfig,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run every strategy/parameter combo; return (all_trades, summary)."""
    all_trades: list[pd.DataFrame] = []
    summary_rows: list[dict] = []

    symbol = df["symbol"].iloc[0] if len(df) else None
    timeframe = df["timeframe"].iloc[0] if len(df) else None

    for strategy_name, params in build_strategy_runs():
        signal_fn = _SIGNAL_FUNCS[strategy_name]
        signals = signal_fn(df, **params)

        trades = backtester.run_backtest(
            df,
            signals,
            config,
            strategy_name=strategy_name,
            symbol=symbol,
            timeframe=timeframe,
        )

        config_id = _config_id(strategy_name, params)
        if len(trades):
            trades = trades.copy()
            trades.insert(0, "config_id", config_id)
            trades["params"] = json.dumps(params)
            all_trades.append(trades)

        run_metrics = metrics.compute_metrics(trades)
        summary_rows.append(
            {
                "config_id": config_id,
                "strategy_name": strategy_name,
                "symbol": symbol,
                "timeframe": timeframe,
                "params": json.dumps(params),
                **{f"param_{k}": v for k, v in params.items()},
                **run_metrics,
            }
        )

    trades_df = (
        pd.concat(all_trades, ignore_index=True)
        if all_trades
        else pd.DataFrame(columns=["config_id", *backtester.TRADE_COLUMNS, "params"])
    )
    summary_df = pd.DataFrame(summary_rows)
    return trades_df, summary_df


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

_LEADERBOARD_COLUMNS = [
    "config_id",
    "total_trades",
    "win_rate",
    "profit_factor",
    "net_profit",
    "max_drawdown",
    "average_r",
]


def _print_leaderboard(summary: pd.DataFrame, by: str, ascending: bool) -> None:
    if summary.empty:
        print(f"\n(no results to rank by {by})")
        return
    ranked = summary.sort_values(by, ascending=ascending).head(10)
    direction = "ascending" if ascending else "descending"
    print(f"\n=== Top 10 by {by} ({direction}) ===")
    with pd.option_context("display.max_columns", None, "display.width", 200):
        print(ranked[_LEADERBOARD_COLUMNS].to_string(index=False))


def print_leaderboards(summary: pd.DataFrame) -> None:
    _print_leaderboard(summary, "profit_factor", ascending=False)
    _print_leaderboard(summary, "net_profit", ascending=False)
    _print_leaderboard(summary, "max_drawdown", ascending=True)
    _print_leaderboard(summary, "total_trades", ascending=False)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Strategy Lab backtest runner")
    parser.add_argument(
        "--data-file",
        type=Path,
        default=DEFAULT_DATA_FILE,
        help="Path to the MT5 CSV export to backtest.",
    )
    parser.add_argument("--atr-period", type=int, default=14)
    parser.add_argument("--stop-loss-atr", type=float, default=2.0)
    parser.add_argument("--take-profit-atr", type=float, default=3.0)
    parser.add_argument("--max-holding-bars", type=int, default=None)
    parser.add_argument(
        "--spread-mode", choices=["fixed", "bar"], default="fixed"
    )
    parser.add_argument("--spread-points", type=float, default=30.0)
    parser.add_argument("--point-value", type=float, default=0.01)
    parser.add_argument("--max-spread-points", type=float, default=100.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    config = BacktestConfig(
        atr_period=args.atr_period,
        stop_loss_atr=args.stop_loss_atr,
        take_profit_atr=args.take_profit_atr,
        max_holding_bars=args.max_holding_bars,
        spread_mode=args.spread_mode,
        spread_points=args.spread_points,
        point_value=args.point_value,
        max_spread_points=args.max_spread_points,
    )

    print(f"Loading {args.data_file} ...")
    df = data_loader.load_mt5_csv(args.data_file)
    print(
        f"Loaded {len(df)} bars "
        f"({df['datetime'].iloc[0]} -> {df['datetime'].iloc[-1]})"
    )
    print(
        f"Spread mode: {config.spread_mode} | "
        f"SL={config.stop_loss_atr}xATR TP={config.take_profit_atr}xATR | "
        f"ATR period={config.atr_period}"
    )

    trades_df, summary_df = run_sweep(df, config)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    trades_path = REPORTS_DIR / "trades.csv"
    summary_path = REPORTS_DIR / "summary.csv"
    trades_df.to_csv(trades_path, index=False)
    summary_df.to_csv(summary_path, index=False)

    print(f"\nWrote {len(trades_df)} trades -> {trades_path}")
    print(f"Wrote {len(summary_df)} strategy results -> {summary_path}")

    print_leaderboards(summary_df)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
