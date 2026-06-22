"""Rule-based signal generators.

Each strategy consumes a clean OHLC DataFrame (as produced by
:mod:`app.strategy_lab.data_loader`) and returns a DataFrame aligned to the
input rows with at least:

    * ``datetime``       - bar timestamp (UTC)
    * ``signal``         - 1 = long, -1 = short, 0 = no signal
    * ``signal_reason``  - short human-readable explanation

Signals are emitted only on the bar where the entry condition *becomes* true
(an event, not a persistent state). Every signal is derived from completed-bar
data; the backtester enters on the *next* bar's open, so there is no lookahead.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

try:  # package import (e.g. ``from app.strategy_lab import strategies``)
    from . import indicators
except ImportError:  # script import (``python .../run_backtests.py``)
    import indicators  # type: ignore[no-redef]


def _empty_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Skeleton signal frame with all-zero signals."""
    return pd.DataFrame(
        {
            "datetime": df["datetime"].to_numpy(),
            "signal": np.zeros(len(df), dtype=int),
            "signal_reason": np.array([""] * len(df), dtype=object),
        }
    )


def supertrend_strategy(
    df: pd.DataFrame,
    atr_period: int = 10,
    multiplier: float = 3.0,
) -> pd.DataFrame:
    """Long when SuperTrend flips bullish, short when it flips bearish."""
    st = indicators.supertrend(df, atr_period=atr_period, multiplier=multiplier)
    direction = st["direction"]
    prev_direction = direction.shift(1)

    flip_up = (direction == 1) & (prev_direction == -1)
    flip_down = (direction == -1) & (prev_direction == 1)

    out = _empty_signals(df)
    out.loc[flip_up.to_numpy(), "signal"] = 1
    out.loc[flip_down.to_numpy(), "signal"] = -1
    out.loc[flip_up.to_numpy(), "signal_reason"] = "supertrend_flip_bullish"
    out.loc[flip_down.to_numpy(), "signal_reason"] = "supertrend_flip_bearish"
    return out


def ema_crossover_strategy(
    df: pd.DataFrame,
    fast_period: int = 20,
    slow_period: int = 50,
) -> pd.DataFrame:
    """Long on fast EMA crossing above slow EMA, short on the reverse."""
    if fast_period >= slow_period:
        raise ValueError("fast_period must be < slow_period")

    fast = indicators.ema(df["close"], fast_period)
    slow = indicators.ema(df["close"], slow_period)
    prev_fast = fast.shift(1)
    prev_slow = slow.shift(1)

    cross_up = (fast > slow) & (prev_fast <= prev_slow)
    cross_down = (fast < slow) & (prev_fast >= prev_slow)

    out = _empty_signals(df)
    out.loc[cross_up.to_numpy(), "signal"] = 1
    out.loc[cross_down.to_numpy(), "signal"] = -1
    out.loc[cross_up.to_numpy(), "signal_reason"] = (
        f"ema{fast_period}_cross_above_ema{slow_period}"
    )
    out.loc[cross_down.to_numpy(), "signal_reason"] = (
        f"ema{fast_period}_cross_below_ema{slow_period}"
    )
    return out


def donchian_breakout_strategy(
    df: pd.DataFrame,
    lookback: int = 20,
) -> pd.DataFrame:
    """Long/short on a close breaking the *previous* Donchian channel.

    The channel is shifted by one bar so the current candle never contributes
    to the level it must break (no lookahead).
    """
    channel = indicators.donchian_channel(df, lookback=lookback)
    prev_upper = channel["upper"].shift(1)
    prev_lower = channel["lower"].shift(1)
    close = df["close"]

    break_up = close > prev_upper
    break_down = close < prev_lower

    out = _empty_signals(df)
    out.loc[break_up.to_numpy(), "signal"] = 1
    out.loc[break_down.to_numpy(), "signal"] = -1
    out.loc[break_up.to_numpy(), "signal_reason"] = f"break_above_donchian{lookback}_high"
    out.loc[break_down.to_numpy(), "signal_reason"] = f"break_below_donchian{lookback}_low"
    return out


def rsi_mean_reversion_strategy(
    df: pd.DataFrame,
    period: int = 14,
    oversold: float = 30.0,
    overbought: float = 70.0,
) -> pd.DataFrame:
    """Long when RSI crosses up out of oversold, short when it crosses down
    out of overbought (mean-reversion)."""
    rsi_values = indicators.rsi(df["close"], period)
    prev_rsi = rsi_values.shift(1)

    cross_up = (prev_rsi < oversold) & (rsi_values >= oversold)
    cross_down = (prev_rsi > overbought) & (rsi_values <= overbought)

    out = _empty_signals(df)
    out.loc[cross_up.to_numpy(), "signal"] = 1
    out.loc[cross_down.to_numpy(), "signal"] = -1
    out.loc[cross_up.to_numpy(), "signal_reason"] = f"rsi{period}_exit_oversold_{oversold:g}"
    out.loc[cross_down.to_numpy(), "signal_reason"] = (
        f"rsi{period}_exit_overbought_{overbought:g}"
    )
    return out
