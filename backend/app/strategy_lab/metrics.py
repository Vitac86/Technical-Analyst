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


# ---------------------------------------------------------------------------
# v1.2 account-based metrics
# ---------------------------------------------------------------------------

# Extended metric set produced by :func:`compute_risk_metrics`. These describe
# an *account* (equity, margin, lots) rather than price-unit PnL.
RISK_METRIC_KEYS: tuple[str, ...] = (
    "initial_equity",
    "final_equity",
    "total_return_pct",
    "net_profit",
    "total_trades",
    "skipped_trades",
    "winning_trades",
    "losing_trades",
    "win_rate",
    "gross_profit",
    "gross_loss",
    "profit_factor",
    "average_trade",
    "median_trade",
    "max_drawdown",
    "max_drawdown_pct",
    "max_equity",
    "min_equity",
    "max_margin_used",
    "min_margin_level",
    "stop_out_count",
    "insufficient_margin_count",
    "average_lots",
    "max_lots",
    "average_r",
    "median_r",
    "max_consecutive_losses",
    "first_trade_time",
    "last_trade_time",
    "long_trades",
    "short_trades",
    "long_net_profit",
    "short_net_profit",
    "long_profit_factor",
    "short_profit_factor",
)


def _profit_factor(net_pnl: pd.Series) -> float:
    """Gross profit / gross loss; +inf if only wins, 0.0 if nothing positive."""
    gross_profit = float(net_pnl[net_pnl > 0].sum())
    gross_loss = float(-net_pnl[net_pnl < 0].sum())
    if gross_loss > 0:
        return gross_profit / gross_loss
    return float("inf") if gross_profit > 0 else 0.0


def _empty_risk_metrics(initial_equity: float, skipped_trades: int) -> dict:
    """Metrics for a run that produced no executed trades."""
    return {
        "initial_equity": float(initial_equity),
        "final_equity": float(initial_equity),
        "total_return_pct": 0.0,
        "net_profit": 0.0,
        "total_trades": 0,
        "skipped_trades": int(skipped_trades),
        "winning_trades": 0,
        "losing_trades": 0,
        "win_rate": 0.0,
        "gross_profit": 0.0,
        "gross_loss": 0.0,
        "profit_factor": 0.0,
        "average_trade": 0.0,
        "median_trade": 0.0,
        "max_drawdown": 0.0,
        "max_drawdown_pct": 0.0,
        "max_equity": float(initial_equity),
        "min_equity": float(initial_equity),
        "max_margin_used": 0.0,
        "min_margin_level": np.nan,
        "stop_out_count": 0,
        "insufficient_margin_count": int(skipped_trades),
        "average_lots": 0.0,
        "max_lots": 0.0,
        "average_r": np.nan,
        "median_r": np.nan,
        "max_consecutive_losses": 0,
        "first_trade_time": pd.NaT,
        "last_trade_time": pd.NaT,
        "long_trades": 0,
        "short_trades": 0,
        "long_net_profit": 0.0,
        "short_net_profit": 0.0,
        "long_profit_factor": 0.0,
        "short_profit_factor": 0.0,
    }


def compute_risk_metrics(
    trades: pd.DataFrame,
    equity_curve: pd.DataFrame,
    skipped: pd.DataFrame | None = None,
    *,
    initial_equity: float,
) -> dict:
    """Account-level metrics for one :mod:`risk_backtester` run.

    ``trades`` / ``skipped`` use the columns produced by
    :func:`app.strategy_lab.risk_backtester.run_risk_backtest`; ``equity_curve``
    supplies the per-bar equity used for drawdown and equity extremes.
    """
    skipped_trades = 0 if skipped is None else int(len(skipped))
    insufficient = (
        0
        if skipped is None or len(skipped) == 0
        else int((skipped["skipped_reason"] == "insufficient_margin").sum())
    )

    # Equity-curve derived figures are valid even with zero trades.
    if equity_curve is not None and len(equity_curve):
        equity = equity_curve["equity"].astype(float)
        final_equity = float(equity.iloc[-1])
        max_equity = float(equity.max())
        min_equity = float(equity.min())
        max_drawdown = float(equity_curve["drawdown"].astype(float).max())
        max_drawdown_pct = float(equity_curve["drawdown_pct"].astype(float).max())
    else:
        final_equity = float(initial_equity)
        max_equity = min_equity = float(initial_equity)
        max_drawdown = max_drawdown_pct = 0.0

    if trades is None or len(trades) == 0:
        base = _empty_risk_metrics(initial_equity, skipped_trades)
        base.update(
            {
                "final_equity": final_equity,
                "total_return_pct": (final_equity - initial_equity)
                / initial_equity
                * 100.0,
                "max_equity": max_equity,
                "min_equity": min_equity,
                "max_drawdown": max_drawdown,
                "max_drawdown_pct": max_drawdown_pct,
                "insufficient_margin_count": insufficient,
            }
        )
        return base

    net_pnl = trades["net_pnl"].astype(float)
    lots = trades["lots"].astype(float)
    r_multiple = trades["r_multiple"].astype(float)
    is_long = trades["direction"] == "long"
    is_short = trades["direction"] == "short"
    entry_time = pd.to_datetime(trades["entry_time"], utc=True)

    gross_profit = float(net_pnl[net_pnl > 0].sum())
    gross_loss = float(-net_pnl[net_pnl < 0].sum())
    net_profit = float(net_pnl.sum())
    total_trades = int(len(net_pnl))
    winning_trades = int((net_pnl > 0).sum())
    losing_trades = int((net_pnl < 0).sum())

    return {
        "initial_equity": float(initial_equity),
        "final_equity": final_equity,
        "total_return_pct": (final_equity - initial_equity) / initial_equity * 100.0,
        "net_profit": net_profit,
        "total_trades": total_trades,
        "skipped_trades": skipped_trades,
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "win_rate": winning_trades / total_trades if total_trades else 0.0,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": _profit_factor(net_pnl),
        "average_trade": float(net_pnl.mean()),
        "median_trade": float(net_pnl.median()),
        "max_drawdown": max_drawdown,
        "max_drawdown_pct": max_drawdown_pct,
        "max_equity": max_equity,
        "min_equity": min_equity,
        "max_margin_used": float(trades["max_margin_used"].astype(float).max()),
        "min_margin_level": float(trades["min_margin_level"].astype(float).min()),
        "stop_out_count": int((trades["exit_reason"] == "stop_out").sum()),
        "insufficient_margin_count": insufficient,
        "average_lots": float(lots.mean()),
        "max_lots": float(lots.max()),
        "average_r": float(r_multiple.mean()),
        "median_r": float(r_multiple.median()),
        "max_consecutive_losses": _max_consecutive_losses(net_pnl),
        "first_trade_time": entry_time.min(),
        "last_trade_time": entry_time.max(),
        "long_trades": int(is_long.sum()),
        "short_trades": int(is_short.sum()),
        "long_net_profit": float(net_pnl[is_long].sum()),
        "short_net_profit": float(net_pnl[is_short].sum()),
        "long_profit_factor": _profit_factor(net_pnl[is_long]),
        "short_profit_factor": _profit_factor(net_pnl[is_short]),
    }


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
