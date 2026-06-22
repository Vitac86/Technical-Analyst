"""Account-based, leverage/margin-aware backtester (Strategy Lab v1.2).

This module is **research only** -- it never places, simulates or routes real
orders. It extends the v1 :mod:`app.strategy_lab.backtester` (which works purely
in price units) with a realistic *account* model so that wide stops, position
sizing, leverage and margin can be studied together:

    * an explicit account equity / balance that compounds across trades,
    * position sizing in lots (fixed lot, fixed notional, or risk-percent),
    * a broker-style margin model with stop-out (forced liquidation),
    * PnL expressed in the account currency (USD by default).

Correctness / no-lookahead rules (identical in spirit to the v1 backtester):

    * A signal on bar ``i`` is entered at the **open of bar ``i + 1``**; a bar
      can never trade on its own close.
    * ATR used for stops and risk-sizing is the ATR of the **signal bar** (the
      last completed bar before entry).
    * Position sizing uses the account equity *before* the trade (no open
      position exists, so equity == balance at that instant) -- never future
      data.
    * Same-candle ambiguity is resolved conservatively: if both stop-loss and
      take-profit are touched in one candle, the stop-loss is assumed first.
    * If a position is still open at the end of the data it is closed at the
      last available close with ``exit_reason="end_of_data"``.

Sizing for XAUUSD assumes 1.0 lot controls ``contract_size`` (100) ounces of
gold and price is quoted in account currency per ounce, so a price move of
``dp`` on ``lots`` lots is worth ``dp * contract_size * lots`` in the account
currency.

Depends on pandas/numpy only and never mutates its inputs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

try:  # package import
    from . import indicators
except ImportError:  # script import
    import indicators  # type: ignore[no-redef]


# Trade record columns, in the exact order requested by the v1.2 spec.
RISK_TRADE_COLUMNS: tuple[str, ...] = (
    "strategy_name",
    "symbol",
    "timeframe",
    "direction",
    "sizing_mode",
    "leverage",
    "lots",
    "notional",
    "required_margin",
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
    "commission",
    "slippage_cost",
    "swap",
    "net_pnl",
    "balance_after_trade",
    "equity_after_trade",
    "return_pct_on_equity",
    "r_multiple",
    "max_floating_profit",
    "max_floating_loss",
    "max_margin_used",
    "min_margin_level",
    "skipped_reason",
)

EQUITY_CURVE_COLUMNS: tuple[str, ...] = (
    "datetime",
    "balance",
    "equity",
    "floating_pnl",
    "margin_used",
    "free_margin",
    "margin_level",
    "open_position_direction",
    "open_position_lots",
    "drawdown",
    "drawdown_pct",
)

SKIPPED_COLUMNS: tuple[str, ...] = (
    "strategy_name",
    "symbol",
    "timeframe",
    "direction",
    "sizing_mode",
    "leverage",
    "entry_time",
    "entry_price",
    "lots",
    "required_margin",
    "free_margin",
    "skipped_reason",
)

_VALID_SIZING = ("fixed_lot", "fixed_notional", "risk_percent")
_VALID_EXIT = ("fixed_atr", "atr_trailing", "time_exit", "opposite_signal")
_VALID_DIRECTION = ("long_only", "short_only", "both")

# One calendar day, used to accrue swap from a datetime difference.
_ONE_DAY = np.timedelta64(1, "D")


def _to_datetime64(series: pd.Series) -> np.ndarray:
    """Return a tz-naive ``datetime64[ns]`` array (UTC wall time).

    pandas returns an object array of ``Timestamp`` from ``to_numpy()`` on a
    tz-aware column, which does not support arithmetic; dropping the tz (after
    converting to UTC) yields a proper ``datetime64`` array we can subtract.
    """
    if isinstance(series.dtype, pd.DatetimeTZDtype):
        series = series.dt.tz_convert("UTC").dt.tz_localize(None)
    return series.to_numpy(dtype="datetime64[ns]")


@dataclass
class RiskConfig:
    """Full configuration for one account-based backtest run.

    The fields are grouped into account, sizing and exit settings. Only the
    fields relevant to the chosen ``sizing_mode`` / ``exit_mode`` are used; the
    rest are ignored, which keeps the runner's parameter grids simple.
    """

    # --- account -----------------------------------------------------------
    initial_equity: float = 10000.0
    account_currency: str = "USD"
    leverage: float = 20.0
    contract_size: float = 100.0  # 1.0 lot XAUUSD controls 100 oz.
    min_lot: float = 0.01
    lot_step: float = 0.01
    max_lot: float = 100.0
    point_value: float = 0.01  # price units per "point" (gold: 1 point = 0.01).
    fixed_spread_points: float = 30.0
    slippage_points: float = 0.0  # per-side execution slippage, in points.
    commission_per_lot_round_turn: float = 0.0
    swap_long_per_lot_per_day: float = 0.0
    swap_short_per_lot_per_day: float = 0.0
    margin_call_level: float = 1.0  # ratio (1.0 == 100%); informational here.
    stop_out_level: float = 0.5  # ratio; liquidate when margin level <= this.

    # --- ATR / direction ---------------------------------------------------
    atr_period: int = 14
    direction_mode: str = "both"  # long_only | short_only | both

    # --- position sizing ---------------------------------------------------
    sizing_mode: str = "fixed_lot"  # fixed_lot | fixed_notional | risk_percent
    fixed_lot: float = 0.01
    notional_usd: float = 10000.0
    risk_percent: float = 0.5  # percent of current equity (e.g. 0.5 == 0.5%).

    # --- exit logic --------------------------------------------------------
    exit_mode: str = "fixed_atr"  # fixed_atr | atr_trailing | time_exit | opposite_signal
    stop_loss_atr: float = 3.0  # used by fixed_atr / time_exit / opposite_signal
    take_profit_atr: Optional[float] = 8.0  # None disables the TP
    initial_stop_loss_atr: float = 3.0  # used by atr_trailing
    trailing_stop_atr: float = 4.0  # used by atr_trailing
    max_holding_bars: Optional[int] = None  # time_exit (required) / opposite_signal

    def __post_init__(self) -> None:
        if self.sizing_mode not in _VALID_SIZING:
            raise ValueError(f"sizing_mode must be one of {_VALID_SIZING}")
        if self.exit_mode not in _VALID_EXIT:
            raise ValueError(f"exit_mode must be one of {_VALID_EXIT}")
        if self.direction_mode not in _VALID_DIRECTION:
            raise ValueError(f"direction_mode must be one of {_VALID_DIRECTION}")
        if self.leverage <= 0:
            raise ValueError("leverage must be positive")
        if self.exit_mode == "time_exit" and not self.max_holding_bars:
            raise ValueError("time_exit requires max_holding_bars")

    # -- derived helpers ----------------------------------------------------
    @property
    def initial_stop_atr(self) -> float:
        """ATR multiple for the initial protective stop, per exit mode."""
        if self.exit_mode == "atr_trailing":
            return self.initial_stop_loss_atr
        return self.stop_loss_atr

    @property
    def spread_price(self) -> float:
        """Round-turn spread expressed in price units (per ounce)."""
        return self.fixed_spread_points * self.point_value

    @property
    def slippage_price(self) -> float:
        """Per-side slippage expressed in price units (per ounce).

        Applied to *both* fills, so a round trip pays it twice (see
        ``run_risk_backtest``).
        """
        return self.slippage_points * self.point_value


# ---------------------------------------------------------------------------
# Position sizing
# ---------------------------------------------------------------------------

def _round_lot_down(raw_lots: float, config: RiskConfig) -> float:
    """Round ``raw_lots`` *down* to ``lot_step`` and clamp to [min_lot, max_lot].

    Rounding down is the conservative choice for risk-percent sizing (it never
    risks more than requested through rounding). ``min_lot`` is then enforced as
    a floor, so an under-sized request is bumped up to the smallest tradable
    lot (this can risk slightly more than the target, which we accept and note).
    """
    if raw_lots <= 0 or not math.isfinite(raw_lots):
        steps = 0
    else:
        # +epsilon guards against float dust (e.g. 0.0299999 -> 3 steps).
        steps = math.floor(raw_lots / config.lot_step + 1e-9)
    lots = round(steps * config.lot_step, 8)
    lots = max(lots, config.min_lot)
    lots = min(lots, config.max_lot)
    return lots


def _compute_lots(
    config: RiskConfig,
    equity: float,
    entry_price: float,
    stop_distance_price: float,
) -> float:
    """Lot size for one entry given the current ``equity`` and stop distance.

    ``stop_distance_price`` is the price distance from entry to the initial
    stop (already ``initial_stop_atr * atr_at_entry``).
    """
    cs = config.contract_size

    if config.sizing_mode == "fixed_lot":
        raw = config.fixed_lot
    elif config.sizing_mode == "fixed_notional":
        # Convert a target notional in account currency to lots at entry price.
        raw = config.notional_usd / (entry_price * cs)
    else:  # risk_percent
        risk_amount = (config.risk_percent / 100.0) * equity
        # Approximate per-lot loss if the stop is hit: price distance plus the
        # round-turn spread, slippage (both fills) and commission realised on
        # exit. Including the costs keeps risk-percent sizing honest under the
        # conservative/stress cost scenarios.
        risk_per_lot = (
            stop_distance_price * cs
            + config.spread_price * cs
            + 2.0 * config.slippage_price * cs
            + config.commission_per_lot_round_turn
        )
        raw = risk_amount / risk_per_lot if risk_per_lot > 0 else 0.0

    return _round_lot_down(raw, config)


# ---------------------------------------------------------------------------
# Exit scan (single trade)
# ---------------------------------------------------------------------------

@dataclass
class _ScanResult:
    exit_idx: int
    exit_price: float
    exit_reason: str
    stop_loss_at_exit: float
    max_floating_profit: float
    max_floating_loss: float
    min_margin_level: float


def _scan_trade(
    *,
    entry_idx: int,
    direction: int,
    entry_price: float,
    lots: float,
    required_margin: float,
    balance: float,
    init_stop_loss: float,
    take_profit: Optional[float],
    trailing_dist: Optional[float],
    round_turn_cost: float,
    swap_rate: float,
    config: RiskConfig,
    signal: np.ndarray,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    times: np.ndarray,
    n: int,
) -> _ScanResult:
    """Walk bars forward from ``entry_idx`` and resolve the exit.

    Floating PnL (used for margin / excursion tracking) is measured in account
    currency and already carries the round-turn cost drag plus accrued swap, so
    it reflects what the account would actually feel.
    """
    cs = config.contract_size
    entry_time = times[entry_idx]
    stop_loss = init_stop_loss
    best_price = entry_price  # most favourable price seen so far (for trailing)
    trailing_moved = False

    max_fav = -math.inf
    max_adv = math.inf
    min_ml = math.inf

    def pnl_at(price: float, days: float) -> float:
        """Account-currency PnL of the open position at ``price``."""
        price_pnl = (price - entry_price) * cs * lots * direction
        return price_pnl - round_turn_cost + swap_rate * lots * days

    def record(value: float) -> None:
        nonlocal max_fav, max_adv, min_ml
        max_fav = max(max_fav, value)
        max_adv = min(max_adv, value)
        min_ml = min(min_ml, (balance + value) / required_margin)

    for j in range(entry_idx, n):
        bars_held = j - entry_idx + 1
        days = float((times[j] - entry_time) / _ONE_DAY)

        # 1. Opposite-signal exit happens at *this* bar's open (the signal fired
        #    on the previous, completed bar). It precedes any intrabar SL/TP.
        if (
            config.exit_mode == "opposite_signal"
            and j > entry_idx
            and signal[j - 1] == -direction
        ):
            value = pnl_at(open_[j], days)
            record(value)
            return _ScanResult(
                j, open_[j], "opposite_signal", stop_loss, max_fav, max_adv, min_ml
            )

        # 2. Stop-loss / take-profit touches against the currently active levels.
        if direction == 1:
            sl_hit = low[j] <= stop_loss
            tp_hit = take_profit is not None and high[j] >= take_profit
        else:
            sl_hit = high[j] >= stop_loss
            tp_hit = take_profit is not None and low[j] <= take_profit

        if sl_hit:  # conservative: stop assumed first even if TP also touched
            value = pnl_at(stop_loss, days)
            record(value)
            reason = (
                "trailing_stop"
                if config.exit_mode == "atr_trailing" and trailing_moved
                else "stop_loss"
            )
            return _ScanResult(
                j, stop_loss, reason, stop_loss, max_fav, max_adv, min_ml
            )
        if tp_hit:
            value = pnl_at(take_profit, days)  # type: ignore[arg-type]
            record(value)
            return _ScanResult(
                j, float(take_profit), "take_profit", stop_loss,
                max_fav, max_adv, min_ml,
            )

        # 3. No SL/TP this bar: fold the full intrabar range and check stop-out.
        pnl_hi = pnl_at(high[j], days)
        pnl_lo = pnl_at(low[j], days)
        max_fav = max(max_fav, pnl_hi, pnl_lo)
        worst = min(pnl_hi, pnl_lo)
        max_adv = min(max_adv, worst)
        ml_worst = (balance + worst) / required_margin
        min_ml = min(min_ml, ml_worst)
        if ml_worst <= config.stop_out_level:
            # Forced liquidation at the conservative (worst) intrabar price.
            worst_price = low[j] if direction == 1 else high[j]
            return _ScanResult(
                j, worst_price, "stop_out", stop_loss, max_fav, max_adv, min_ml
            )

        # 4. Time / max-holding exit at the bar close.
        if config.max_holding_bars is not None and bars_held >= config.max_holding_bars:
            return _ScanResult(
                j, close[j], "time_exit", stop_loss, max_fav, max_adv, min_ml
            )

        # 5. Update the trailing stop for the *next* bar (favourable side only).
        if config.exit_mode == "atr_trailing" and trailing_dist is not None:
            if direction == 1:
                best_price = max(best_price, high[j])
                new_stop = best_price - trailing_dist
                if new_stop > stop_loss:
                    stop_loss = new_stop
                    trailing_moved = True
            else:
                best_price = min(best_price, low[j])
                new_stop = best_price + trailing_dist
                if new_stop < stop_loss:
                    stop_loss = new_stop
                    trailing_moved = True

    # Ran out of data while still open -> close at the last available close.
    last = n - 1
    days = float((times[last] - entry_time) / _ONE_DAY)
    record(pnl_at(close[last], days))
    return _ScanResult(
        last, close[last], "end_of_data", stop_loss, max_fav, max_adv, min_ml
    )


# ---------------------------------------------------------------------------
# Equity curve (vectorised second pass)
# ---------------------------------------------------------------------------

def _build_equity_curve(
    trades: list[dict],
    *,
    times: np.ndarray,
    close: np.ndarray,
    config: RiskConfig,
    n: int,
) -> pd.DataFrame:
    """Build the per-bar equity curve from the (non-overlapping) trade list.

    Realised PnL is booked on each trade's exit bar; while a trade is open the
    floating PnL is marked to the bar *close* (the conventional equity-curve
    mark). Per-trade worst-case excursions live on the trades frame instead.
    """
    cs = config.contract_size
    booked = np.zeros(n)
    for t in trades:
        booked[t["exit_idx"]] += t["net_pnl"]
    # cumsum places each trade's PnL only from its exit bar onward, so bars
    # *inside* a trade automatically show the pre-trade (before) balance.
    balance = config.initial_equity + np.cumsum(booked)

    floating = np.zeros(n)
    margin_used = np.zeros(n)
    pos_dir = np.zeros(n, dtype=int)
    pos_lots = np.zeros(n)

    for t in trades:
        s, e = t["entry_idx"], t["exit_idx"]
        if e > s:  # bars the position is genuinely open (exit bar is "closed")
            days = (times[s:e] - times[s]) / _ONE_DAY
            floating[s:e] = (
                (close[s:e] - t["entry_price"]) * cs * t["lots"] * t["dir"]
                - t["round_turn_cost"]
                + t["swap_rate"] * t["lots"] * days
            )
            margin_used[s:e] = t["required_margin"]
            pos_dir[s:e] = t["dir"]
            pos_lots[s:e] = t["lots"]

    equity = balance + floating
    free_margin = equity - margin_used
    with np.errstate(divide="ignore", invalid="ignore"):
        margin_level = np.where(margin_used > 0, equity / margin_used, np.nan)

    peak = np.maximum.accumulate(equity)
    drawdown = peak - equity  # >= 0 magnitude
    drawdown_pct = np.where(peak > 0, drawdown / peak * 100.0, 0.0)

    direction_label = np.where(pos_dir == 1, "long", np.where(pos_dir == -1, "short", ""))

    return pd.DataFrame(
        {
            "datetime": times,
            "balance": balance,
            "equity": equity,
            "floating_pnl": floating,
            "margin_used": margin_used,
            "free_margin": free_margin,
            "margin_level": margin_level,
            "open_position_direction": direction_label,
            "open_position_lots": pos_lots,
            "drawdown": drawdown,
            "drawdown_pct": drawdown_pct,
        },
        columns=list(EQUITY_CURVE_COLUMNS),
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def _direction_allowed(signal_value: int, direction_mode: str) -> bool:
    if direction_mode == "long_only":
        return signal_value == 1
    if direction_mode == "short_only":
        return signal_value == -1
    return signal_value != 0


def run_risk_backtest(
    df: pd.DataFrame,
    signals: pd.DataFrame,
    config: RiskConfig | None = None,
    *,
    strategy_name: str = "strategy",
    symbol: str | None = None,
    timeframe: str | None = None,
    atr_values: np.ndarray | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Run one account-based backtest.

    Returns ``(trades_df, equity_curve_df, skipped_df)``. ``df`` and ``signals``
    must be row-aligned. ``atr_values`` may be supplied pre-computed (the runner
    reuses it across many configs on the same timeframe) to avoid recomputation.
    """
    config = config or RiskConfig()
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
    times = _to_datetime64(df["datetime"])  # datetime64[ns], UTC wall time
    signal = signals["signal"].to_numpy(dtype=int)

    if atr_values is None:
        atr_values = indicators.atr(df, config.atr_period).to_numpy(dtype=float)

    n = len(df)
    cs = config.contract_size
    spread_cost_per_lot = config.spread_price * cs  # round-turn, account currency
    # Slippage worsens BOTH the entry and the exit fill by ``slippage_points``
    # each (a long buys higher and sells lower; a short sells lower and buys
    # higher), so the round-turn drag is twice the per-side slippage. Modelling
    # it as an account-currency cost is exactly equivalent to shifting both fill
    # prices for PnL purposes and keeps it consistent with the spread model.
    slippage_cost_per_lot = 2.0 * config.slippage_price * cs  # round-turn
    direction_label = {1: "long", -1: "short"}

    balance = config.initial_equity
    trades: list[dict] = []
    skipped: list[dict] = []

    i = 0
    # A signal on the last bar cannot be entered (no next bar) -> n - 1.
    while i < n - 1:
        sig = signal[i]
        if sig == 0 or not _direction_allowed(sig, config.direction_mode):
            i += 1
            continue

        direction = int(sig)
        entry_idx = i + 1
        atr_at_entry = atr_values[i]
        if not np.isfinite(atr_at_entry) or atr_at_entry <= 0:
            i += 1
            continue

        entry_price = open_[entry_idx]
        stop_distance = config.initial_stop_atr * atr_at_entry

        # Sizing uses equity *before* the trade (no open position => equity==balance).
        lots = _compute_lots(config, balance, entry_price, stop_distance)
        notional = entry_price * cs * lots
        required_margin = notional / config.leverage
        free_margin = balance  # no open position at this instant

        if required_margin > free_margin:
            skipped.append(
                {
                    "strategy_name": strategy_name,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "direction": direction_label[direction],
                    "sizing_mode": config.sizing_mode,
                    "leverage": config.leverage,
                    "entry_time": times[entry_idx],
                    "entry_price": entry_price,
                    "lots": lots,
                    "required_margin": required_margin,
                    "free_margin": free_margin,
                    "skipped_reason": "insufficient_margin",
                }
            )
            i += 1
            continue

        # Initial protective levels (price units).
        take_profit_atr = config.take_profit_atr
        if direction == 1:
            init_stop = entry_price - stop_distance
            take_profit = (
                entry_price + take_profit_atr * atr_at_entry
                if take_profit_atr
                else None
            )
        else:
            init_stop = entry_price + stop_distance
            take_profit = (
                entry_price - take_profit_atr * atr_at_entry
                if take_profit_atr
                else None
            )

        trailing_dist = (
            config.trailing_stop_atr * atr_at_entry
            if config.exit_mode == "atr_trailing"
            else None
        )

        round_turn_cost = (
            spread_cost_per_lot * lots
            + slippage_cost_per_lot * lots
            + config.commission_per_lot_round_turn * lots
        )
        swap_rate = (
            config.swap_long_per_lot_per_day
            if direction == 1
            else config.swap_short_per_lot_per_day
        )

        result = _scan_trade(
            entry_idx=entry_idx,
            direction=direction,
            entry_price=entry_price,
            lots=lots,
            required_margin=required_margin,
            balance=balance,
            init_stop_loss=init_stop,
            take_profit=take_profit,
            trailing_dist=trailing_dist,
            round_turn_cost=round_turn_cost,
            swap_rate=swap_rate,
            config=config,
            signal=signal,
            open_=open_,
            high=high,
            low=low,
            close=close,
            times=times,
            n=n,
        )

        exit_time = times[result.exit_idx]
        days_held = float((exit_time - times[entry_idx]) / _ONE_DAY)

        gross_pnl = (result.exit_price - entry_price) * cs * lots * direction
        spread_cost = spread_cost_per_lot * lots
        commission = config.commission_per_lot_round_turn * lots
        slippage_cost = slippage_cost_per_lot * lots
        swap = swap_rate * lots * days_held
        net_pnl = gross_pnl - spread_cost - commission - slippage_cost + swap

        balance_before = balance
        balance += net_pnl

        initial_risk_dollars = stop_distance * cs * lots
        r_multiple = net_pnl / initial_risk_dollars if initial_risk_dollars > 0 else np.nan
        return_pct = net_pnl / balance_before * 100.0 if balance_before > 0 else np.nan

        trades.append(
            {
                # --- output fields ---
                "strategy_name": strategy_name,
                "symbol": symbol,
                "timeframe": timeframe,
                "direction": direction_label[direction],
                "sizing_mode": config.sizing_mode,
                "leverage": config.leverage,
                "lots": lots,
                "notional": notional,
                "required_margin": required_margin,
                "entry_time": times[entry_idx],
                "entry_price": entry_price,
                "exit_time": exit_time,
                "exit_price": result.exit_price,
                "stop_loss": result.stop_loss_at_exit,
                "take_profit": take_profit if take_profit is not None else np.nan,
                "exit_reason": result.exit_reason,
                "bars_held": result.exit_idx - entry_idx + 1,
                "gross_pnl": gross_pnl,
                "spread_cost": spread_cost,
                "commission": commission,
                "slippage_cost": slippage_cost,
                "swap": swap,
                "net_pnl": net_pnl,
                "balance_after_trade": balance,
                "equity_after_trade": balance,  # flat after close (one position at a time)
                "return_pct_on_equity": return_pct,
                "r_multiple": r_multiple,
                "max_floating_profit": result.max_floating_profit,
                "max_floating_loss": result.max_floating_loss,
                "max_margin_used": required_margin,
                "min_margin_level": result.min_margin_level,
                "skipped_reason": "",
                # --- internal fields for the equity-curve pass (dropped later) ---
                "entry_idx": entry_idx,
                "exit_idx": result.exit_idx,
                "dir": direction,
                "round_turn_cost": round_turn_cost,
                "swap_rate": swap_rate,
            }
        )

        # Resume from the exit bar; the next entry is the next signal after it,
        # so positions never overlap (exit_idx > i guarantees progress).
        i = result.exit_idx

    equity_curve = _build_equity_curve(
        trades, times=times, close=close, config=config, n=n
    )

    if trades:
        trades_df = pd.DataFrame(trades)[list(RISK_TRADE_COLUMNS)]
    else:
        trades_df = pd.DataFrame(columns=list(RISK_TRADE_COLUMNS))

    skipped_df = (
        pd.DataFrame(skipped)[list(SKIPPED_COLUMNS)]
        if skipped
        else pd.DataFrame(columns=list(SKIPPED_COLUMNS))
    )

    return trades_df, equity_curve, skipped_df
