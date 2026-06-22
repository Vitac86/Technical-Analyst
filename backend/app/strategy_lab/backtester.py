"""A small event-driven backtester for single-instrument signal frames.

Design choices (v1):

* One position at a time; both long and short are supported.
* A signal on bar ``i`` is executed at the **open of bar ``i + 1``** so a bar
  can never be used to trade on its own close (no lookahead).
* Stop-loss and take-profit are placed at ATR multiples measured from the entry
  price, using the ATR value of the *signal* bar (the last completed bar before
  entry).
* If both SL and TP fall inside the same candle, the exit is resolved
  conservatively: the stop-loss is assumed to be hit first for both long and
  short trades (we cannot see intrabar order).
* Spread is charged once per round-trip trade, in price units.

PnL is expressed in price units (quote currency per 1 unit of the instrument);
position sizing / lots are intentionally out of scope for v1.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

try:  # package import
    from . import indicators
except ImportError:  # script import
    import indicators  # type: ignore[no-redef]


TRADE_COLUMNS: tuple[str, ...] = (
    "strategy_name",
    "symbol",
    "timeframe",
    "direction",
    "entry_time",
    "entry_price",
    "exit_time",
    "exit_price",
    "stop_loss",
    "take_profit",
    "exit_reason",
    "bars_held",
    "gross_pnl",
    "spread_cost",
    "net_pnl",
    "r_multiple",
)


@dataclass
class BacktestConfig:
    """Configuration for a backtest run.

    Spread modelling:
        * ``spread_mode="fixed"`` - always charge ``spread_points`` (default).
        * ``spread_mode="bar"``   - use each bar's own ``spread`` column and
          skip entries whose spread is missing or above ``max_spread_points``.
    """

    atr_period: int = 14
    stop_loss_atr: float = 2.0
    take_profit_atr: float = 3.0
    max_holding_bars: int | None = None

    spread_mode: str = "fixed"  # "fixed" | "bar"
    spread_points: float = 30.0
    point_value: float = 0.01
    max_spread_points: float = 100.0

    def __post_init__(self) -> None:
        if self.spread_mode not in ("fixed", "bar"):
            raise ValueError("spread_mode must be 'fixed' or 'bar'")
        if self.stop_loss_atr <= 0 or self.take_profit_atr <= 0:
            raise ValueError("stop_loss_atr and take_profit_atr must be positive")


def _resolve_entry_spread_points(
    config: BacktestConfig,
    bar_spread: float,
) -> float | None:
    """Return the spread (in points) to charge for an entry, or ``None`` to skip.

    In ``fixed`` mode the configured spread is always used. In ``bar`` mode the
    bar's own spread is used, but the entry is skipped when the spread is
    unavailable or exceeds ``max_spread_points``.
    """
    if config.spread_mode == "fixed":
        return config.spread_points

    # bar mode
    if bar_spread is None or np.isnan(bar_spread):
        return None
    if bar_spread > config.max_spread_points:
        return None
    return float(bar_spread)


def run_backtest(
    df: pd.DataFrame,
    signals: pd.DataFrame,
    config: BacktestConfig | None = None,
    *,
    strategy_name: str = "strategy",
    symbol: str | None = None,
    timeframe: str | None = None,
) -> pd.DataFrame:
    """Run the backtester over ``df`` using ``signals``.

    ``df`` and ``signals`` must be row-aligned (same order/length); both are
    expected to come from the same source frame. Returns a trades DataFrame
    with the columns listed in :data:`TRADE_COLUMNS`.
    """
    config = config or BacktestConfig()

    if len(df) != len(signals):
        raise ValueError("df and signals must have the same number of rows")

    if symbol is None and "symbol" in df.columns and len(df):
        symbol = df["symbol"].iloc[0]
    if timeframe is None and "timeframe" in df.columns and len(df):
        timeframe = df["timeframe"].iloc[0]

    open_ = df["open"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    times = df["datetime"].to_numpy()

    if "spread" in df.columns:
        spread = df["spread"].to_numpy(dtype=float)
    else:
        spread = np.full(len(df), np.nan)

    atr_values = indicators.atr(df, config.atr_period).to_numpy(dtype=float)
    signal = signals["signal"].to_numpy(dtype=int)

    point_value = config.point_value
    n = len(df)
    trades: list[dict] = []

    i = 0
    # A signal on the last bar cannot be entered (no next bar), hence n - 1.
    while i < n - 1:
        if signal[i] == 0:
            i += 1
            continue

        direction = int(signal[i])
        entry_idx = i + 1

        atr_at_entry = atr_values[i]
        if np.isnan(atr_at_entry) or atr_at_entry <= 0:
            i += 1
            continue

        spread_points = _resolve_entry_spread_points(config, spread[entry_idx])
        if spread_points is None:
            i += 1
            continue

        entry_price = open_[entry_idx]
        risk = config.stop_loss_atr * atr_at_entry
        reward = config.take_profit_atr * atr_at_entry

        if direction == 1:
            stop_loss = entry_price - risk
            take_profit = entry_price + reward
        else:
            stop_loss = entry_price + risk
            take_profit = entry_price - reward

        exit_idx, exit_price, exit_reason = _scan_for_exit(
            entry_idx=entry_idx,
            direction=direction,
            stop_loss=stop_loss,
            take_profit=take_profit,
            high=high,
            low=low,
            close=close,
            n=n,
            max_holding_bars=config.max_holding_bars,
        )

        gross_pnl = (
            (exit_price - entry_price)
            if direction == 1
            else (entry_price - exit_price)
        )
        spread_cost = spread_points * point_value
        net_pnl = gross_pnl - spread_cost
        r_multiple = net_pnl / risk if risk > 0 else np.nan

        trades.append(
            {
                "strategy_name": strategy_name,
                "symbol": symbol,
                "timeframe": timeframe,
                "direction": "long" if direction == 1 else "short",
                "entry_time": times[entry_idx],
                "entry_price": entry_price,
                "exit_time": times[exit_idx],
                "exit_price": exit_price,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "exit_reason": exit_reason,
                "bars_held": exit_idx - entry_idx + 1,
                "gross_pnl": gross_pnl,
                "spread_cost": spread_cost,
                "net_pnl": net_pnl,
                "r_multiple": r_multiple,
            }
        )

        # Resume the search at the exit bar; the next entry can only be the bar
        # after, so positions never overlap. exit_idx > i guarantees progress.
        i = exit_idx

    return pd.DataFrame(trades, columns=list(TRADE_COLUMNS))


def _scan_for_exit(
    *,
    entry_idx: int,
    direction: int,
    stop_loss: float,
    take_profit: float,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    n: int,
    max_holding_bars: int | None,
) -> tuple[int, float, str]:
    """Walk bars forward from the entry to find the exit.

    Returns ``(exit_idx, exit_price, exit_reason)``. The entry bar itself is
    eligible for an intrabar SL/TP touch.
    """
    for j in range(entry_idx, n):
        bars_held = j - entry_idx + 1

        if direction == 1:
            sl_hit = low[j] <= stop_loss
            tp_hit = high[j] >= take_profit
        else:
            sl_hit = high[j] >= stop_loss
            tp_hit = low[j] <= take_profit

        if sl_hit and tp_hit:
            # Ambiguous intrabar order -> assume the stop is hit first.
            return j, stop_loss, "stop_loss"
        if sl_hit:
            return j, stop_loss, "stop_loss"
        if tp_hit:
            return j, take_profit, "take_profit"

        if max_holding_bars is not None and bars_held >= max_holding_bars:
            return j, close[j], "max_holding"

    # Ran out of data while still in the position.
    last = n - 1
    return last, close[last], "end_of_data"
