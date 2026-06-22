"""Performance metrics computed from a trades DataFrame.

The functions here are agnostic to the strategy; they only need the columns
produced by :mod:`app.strategy_lab.backtester` (notably ``net_pnl``,
``r_multiple`` and ``entry_time``). All monetary figures are in the same price
units as the trades themselves.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

METRIC_KEYS: tuple[str, ...] = (
    "total_trades",
    "winning_trades",
    "losing_trades",
    "win_rate",
    "gross_profit",
    "gross_loss",
    "net_profit",
    "profit_factor",
    "average_trade",
    "median_trade",
    "max_drawdown",
    "average_r",
    "median_r",
    "max_consecutive_losses",
    "first_trade_time",
    "last_trade_time",
)


def _empty_metrics() -> dict:
    """Metrics for an empty trade set (no trades taken)."""
    return {
        "total_trades": 0,
        "winning_trades": 0,
        "losing_trades": 0,
        "win_rate": 0.0,
        "gross_profit": 0.0,
        "gross_loss": 0.0,
        "net_profit": 0.0,
        "profit_factor": 0.0,
        "average_trade": 0.0,
        "median_trade": 0.0,
        "max_drawdown": 0.0,
        "average_r": np.nan,
        "median_r": np.nan,
        "max_consecutive_losses": 0,
        "first_trade_time": pd.NaT,
        "last_trade_time": pd.NaT,
    }


def _max_consecutive_losses(net_pnl: pd.Series) -> int:
    """Longest run of consecutive losing trades (net_pnl < 0)."""
    losing = (net_pnl < 0).to_numpy()
    longest = current = 0
    for is_loss in losing:
        current = current + 1 if is_loss else 0
        longest = max(longest, current)
    return int(longest)


def _max_drawdown(net_pnl: pd.Series) -> float:
    """Peak-to-trough drawdown of the cumulative (per-trade) equity curve.

    Returned as a positive magnitude.
    """
    equity = net_pnl.cumsum()
    running_max = equity.cummax()
    drawdown = equity - running_max  # <= 0
    return float(-drawdown.min()) if len(drawdown) else 0.0


def compute_metrics(trades: pd.DataFrame) -> dict:
    """Compute the v1 performance metric set from a trades DataFrame."""
    if trades is None or len(trades) == 0:
        return _empty_metrics()

    net_pnl = trades["net_pnl"].astype(float)
    wins = net_pnl[net_pnl > 0]
    losses = net_pnl[net_pnl < 0]

    gross_profit = float(wins.sum())
    gross_loss = float(-losses.sum())  # positive magnitude of losses
    net_profit = float(net_pnl.sum())

    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    elif gross_profit > 0:
        profit_factor = float("inf")
    else:
        profit_factor = 0.0

    total_trades = int(len(net_pnl))
    winning_trades = int((net_pnl > 0).sum())
    losing_trades = int((net_pnl < 0).sum())

    entry_time = pd.to_datetime(trades["entry_time"], utc=True)

    r_multiple = trades["r_multiple"].astype(float)

    return {
        "total_trades": total_trades,
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "win_rate": winning_trades / total_trades if total_trades else 0.0,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "net_profit": net_profit,
        "profit_factor": profit_factor,
        "average_trade": float(net_pnl.mean()),
        "median_trade": float(net_pnl.median()),
        "max_drawdown": _max_drawdown(net_pnl),
        "average_r": float(r_multiple.mean()),
        "median_r": float(r_multiple.median()),
        "max_consecutive_losses": _max_consecutive_losses(net_pnl),
        "first_trade_time": entry_time.min(),
        "last_trade_time": entry_time.max(),
    }
