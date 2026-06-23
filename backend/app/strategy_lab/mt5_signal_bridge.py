"""Strategy Lab v1.7: MetaTrader 5 **signal-only** bridge.

This module connects to a *locally running* MetaTrader 5 terminal, reads a
Strategy Lab v1.6 exported strategy config, pulls recent candles, and computes
the **exact same** rule-based signal as the Strategy Lab backtester. It then
writes an alert / log entry. It is a research/monitoring tool only.

    v1.7 is signal-only. Execution is intentionally disabled.

Hard safety guarantees (see :data:`EXECUTION_ENABLED` and
:func:`assert_signal_only`):

    * It NEVER opens, closes or modifies orders/positions (no order-placement or
      trade/position calls are referenced anywhere in this package).
    * It NEVER handles a broker login/password and never stores credentials --
      it attaches to the already-running, already-logged-in local terminal.
    * It NEVER enables live trading: ``execution_enabled`` is always ``False``.

Why Python (not MQL5)? Reusing :mod:`app.strategy_lab.presets`,
:mod:`app.strategy_lab.strategies` and :mod:`app.strategy_lab.indicators` keeps
the live signal *byte-for-byte* aligned with the backtester -- there is no
duplicated indicator/strategy logic to drift.

No-lookahead / closed-candle rule
---------------------------------
The currently forming candle is never used. We fetch ``bars`` candles, treat the
last MT5 bar as possibly incomplete, drop it, and evaluate the signal on the
**second-to-last** bar (the latest *closed* candle). This mirrors the
backtester, where a signal on a completed bar ``i`` is acted on at the open of
bar ``i + 1`` -- hence ``suggested_entry_reference == "next_bar_open_or_market"``.

Timezone assumption
-------------------
MT5 ``rates['time']`` is epoch seconds in the terminal/server clock. We convert
it with ``unit="s"`` to **naive, UTC-like** timestamps and treat them as UTC
wall time, consistent with :mod:`app.strategy_lab.risk_backtester`. Only OHLC is
used for the signal maths; the timestamp is carried through as ``signal_time``.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

try:  # package import
    from . import indicators, presets
except ImportError:  # script import (``python .../run_mt5_signal_bridge.py``)
    import indicators  # type: ignore[no-redef]
    import presets  # type: ignore[no-redef]


# ---------------------------------------------------------------------------
# Safety locks -- v1.7 is signal-only. Execution is intentionally disabled.
# ---------------------------------------------------------------------------
# v1.7 is signal-only. Execution is intentionally disabled. This flag must stay
# False; nothing in this package may flip it or call any trade/order function.
EXECUTION_ENABLED: bool = False


def assert_signal_only() -> None:
    """Runtime guard: v1.7 is signal-only. Execution is intentionally disabled.

    Raised before any signal evaluation so the bridge fails loudly if the
    execution lock were ever tampered with.
    """
    if EXECUTION_ENABLED:  # pragma: no cover - lock must never be flipped in v1.7
        raise RuntimeError(
            "v1.7 is signal-only. Execution is intentionally disabled."
        )


class BridgeError(RuntimeError):
    """A user-facing bridge error (bad config, MT5 unavailable, no data)."""


# ---------------------------------------------------------------------------
# Supported strategies (must match Strategy Lab v1.6 presets)
# ---------------------------------------------------------------------------
# Each confirmed rule-based finalist is locked to the timeframe it was confirmed
# on. v1.7 focuses on D by default; C is supported with the same guarantees.
STRATEGY_TIMEFRAMES: dict[str, str] = {
    "D_supertrend_h4_trailing_risk": "H4",
    "C_donchian_h1_fixed_atr_risk": "H1",
}

DEFAULT_STRATEGY_ID: str = "D_supertrend_h4_trailing_risk"
DEFAULT_BARS: int = 500
SUGGESTED_ENTRY_REFERENCE: str = "next_bar_open_or_market"
SIGNAL_STATUS: str = "signal_only"

# v1.7.2+ enriched trading plan: a *reference* entry, never an order instruction.
REFERENCE_ENTRY_TYPE: str = "next_bar_open_or_market_reference"

# Recent-candle diagnostics (signal-only): how many closed candles to report.
DEFAULT_RECENT_LIMIT: int = 10
MAX_RECENT_LIMIT: int = 100

SUPER_TREND_NEXT_BUY_CONDITION: str = (
    "A BUY signal appears only after a fresh bullish SuperTrend flip on a fully "
    "closed H4 candle. Price must close above the current SuperTrend reference "
    "boundary and the signal must be new, not a repeated bullish state. The "
    "current reference boundary can move as new candles form."
)
DONCHIAN_NEXT_BUY_CONDITION: str = (
    "A BUY signal appears when a fully closed H1 candle breaks above the "
    "Donchian high used by the strategy."
)

# Fallbacks used only when MT5 symbol/account specs are unavailable (e.g. tests
# or an offline check). In live use these come from MT5 symbol_info/account_info.
DEFAULT_CONTRACT_SIZE: float = 100.0  # XAUUSD: 100 oz per 1.00 lot.
DEFAULT_LOT_STEP: float = 0.01

# Stable field order for the *full* emitted signal record's flat identity fields.
# The enriched record also carries nested ``market_snapshot`` / ``strategy_state``
# / ``trading_plan`` objects (see :func:`build_signal_record`).
SIGNAL_FIELDS: tuple[str, ...] = (
    "signal_id",
    "generated_at",
    "symbol",
    "timeframe",
    "strategy_id",
    "signal_time",
    "signal_type",
    "reason",
    "close_price",
    "atr_value",
    "suggested_entry_reference",
    "risk_percent",
    "initial_stop_loss_atr",
    "trailing_stop_atr",
    "take_profit_atr",
    "status",
    "execution_enabled",
)

# Flattened columns for signals.csv: key identity fields plus the most useful
# trading-plan references (no order is ever placed -- these are signal-only).
SIGNAL_CSV_FIELDS: tuple[str, ...] = (
    "signal_id",
    "generated_at",
    "signal_time",
    "symbol",
    "timeframe",
    "strategy_id",
    "signal_type",
    "reason",
    "reason_human",
    "close_price",
    "atr_value",
    "strategy_regime",
    "buy_zone_level",
    "distance_to_buy_zone_price",
    "distance_to_buy_zone_atr",
    "distance_to_buy_zone_pct",
    "buy_zone_relation",
    "reference_entry_price",
    "initial_stop_price",
    "trailing_stop_reference",
    "take_profit_price",
    "risk_percent",
    "suggested_lot",
    "execution_enabled",
)

# MT5 rates -> bridge DataFrame columns (data_loader/strategies-compatible).
RATES_COLUMNS: tuple[str, ...] = (
    "datetime",
    "open",
    "high",
    "low",
    "close",
    "tick_volume",
    "spread",
    "real_volume",
)


# ---------------------------------------------------------------------------
# Config input (Strategy Lab v1.6 exported JSON)
# ---------------------------------------------------------------------------
def load_config(path: str | Path) -> dict:
    """Read and parse an exported v1.6 strategy config JSON file."""
    config_path = Path(path)
    if not config_path.exists():
        raise BridgeError(f"Strategy config not found: {config_path}")
    try:
        with config_path.open("r", encoding="utf-8") as handle:
            config = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise BridgeError(f"Could not read strategy config {config_path}: {exc}") from exc
    if not isinstance(config, dict):
        raise BridgeError("Strategy config must be a JSON object.")
    return config


def validate_config(config: dict) -> None:
    """Validate an exported config for the v1.7 signal-only bridge.

    Enforces the v1.7 contract: ML disabled, long-only, a supported rule-based
    strategy, and the strategy's locked timeframe (H4 for D, H1 for C).
    """
    strategy_id = config.get("strategy_id")
    if strategy_id not in STRATEGY_TIMEFRAMES:
        valid = ", ".join(STRATEGY_TIMEFRAMES)
        raise BridgeError(
            f"Unsupported strategy_id '{strategy_id}'. v1.7 supports: {valid}."
        )

    # Confirm the preset still exists in the Strategy Lab (catches drift early).
    try:
        presets.get_preset(strategy_id)
    except KeyError as exc:  # pragma: no cover - defensive
        raise BridgeError(str(exc)) from exc

    if config.get("ml_filter_enabled") is not False:
        raise BridgeError(
            "ml_filter_enabled must be false. The ML filter is research-only and "
            "is not supported by the signal bridge."
        )

    direction_mode = config.get("direction_mode")
    if direction_mode != "long_only":
        raise BridgeError(
            f"direction_mode must be 'long_only' (got '{direction_mode}')."
        )

    expected_tf = STRATEGY_TIMEFRAMES[strategy_id]
    timeframe = str(config.get("timeframe", "")).upper()
    if timeframe != expected_tf:
        raise BridgeError(
            f"timeframe must be '{expected_tf}' for {strategy_id} (got '{timeframe}')."
        )


# ---------------------------------------------------------------------------
# MT5 connection (signal-only: connect to the running terminal, never log in)
# ---------------------------------------------------------------------------
def load_mt5():  # type: ignore[no-untyped-def]
    """Import the ``MetaTrader5`` package or fail with a clear install hint."""
    try:
        import MetaTrader5 as mt5  # type: ignore[import-not-found]
    except ImportError as exc:
        raise BridgeError(
            "The MetaTrader5 Python package is not installed. Install it with:\n"
            "    pip install MetaTrader5"
        ) from exc
    return mt5


def initialize_mt5(mt5) -> None:  # type: ignore[no-untyped-def]
    """Attach to the already-running local MT5 terminal.

    No login/password is ever passed: the bridge relies on a terminal the user
    has already opened and logged in. We never store or read credentials.
    """
    if not mt5.initialize():
        code, message = mt5.last_error()
        raise BridgeError(
            f"mt5.initialize() failed: ({code}) {message}. "
            "Make sure the MetaTrader 5 terminal is running and logged in."
        )


def shutdown_mt5(mt5) -> None:  # type: ignore[no-untyped-def]
    """Release the MT5 connection (safe to call even if not initialised)."""
    try:
        mt5.shutdown()
    except Exception:  # pragma: no cover - shutdown best-effort
        pass


def _mt5_timeframe(mt5, timeframe: str):  # type: ignore[no-untyped-def]
    """Map an ``H4``/``H1`` string to the MT5 ``TIMEFRAME_*`` constant."""
    attr = f"TIMEFRAME_{timeframe.upper()}"
    constant = getattr(mt5, attr, None)
    if constant is None:
        raise BridgeError(f"Unsupported MT5 timeframe '{timeframe}'.")
    return constant


def resolve_symbol(mt5, symbol: str) -> str:  # type: ignore[no-untyped-def]
    """Resolve ``symbol`` to a tradable MT5 symbol, trying the ``rfd`` variant.

    Many brokers expose gold as ``XAUUSDrfd`` rather than ``XAUUSD``; this lets
    a config that says ``XAUUSD`` work against an ``XAUUSDrfd`` feed. The symbol
    is selected into Market Watch (read-only) so ``copy_rates_*`` can see it.
    """
    for candidate in (symbol, f"{symbol}rfd"):
        info = mt5.symbol_info(candidate)
        if info is not None:
            if not getattr(info, "visible", True):
                mt5.symbol_select(candidate, True)
            return candidate
    raise BridgeError(
        f"Symbol '{symbol}' not found in MT5. Pass the broker's exact name with "
        "--symbol (e.g. --symbol XAUUSDrfd)."
    )


def fetch_rates(mt5, symbol: str, timeframe: str, bars: int):  # type: ignore[no-untyped-def]
    """Pull the most recent ``bars`` candles via ``copy_rates_from_pos``.

    Returns the raw MT5 rates structure (a numpy structured array). Position 0
    is the current, possibly-forming candle; the closed-candle rule drops it.
    """
    tf = _mt5_timeframe(mt5, timeframe)
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, int(bars))
    if rates is None or len(rates) == 0:
        code, message = mt5.last_error()
        raise BridgeError(
            f"copy_rates_from_pos returned no data for {symbol} {timeframe}: "
            f"({code}) {message}."
        )
    return rates


def rates_to_dataframe(rates) -> pd.DataFrame:  # type: ignore[no-untyped-def]
    """Convert MT5 rates to the DataFrame shape used by data_loader/strategies.

    Output columns: ``datetime, open, high, low, close, tick_volume, spread,
    real_volume``. ``datetime`` is naive UTC-like (see module docstring).
    """
    df = pd.DataFrame(rates)
    if "time" not in df.columns:
        raise BridgeError("MT5 rates are missing the 'time' field.")
    df["datetime"] = pd.to_datetime(df["time"], unit="s")  # naive, UTC-like
    for col in ("open", "high", "low", "close", "tick_volume", "spread", "real_volume"):
        if col not in df.columns:
            df[col] = 0  # tolerate brokers that omit optional volume/spread fields
    return df.loc[:, list(RATES_COLUMNS)].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Closed-candle rule
# ---------------------------------------------------------------------------
def select_closed_candles(df: pd.DataFrame) -> pd.DataFrame:
    """Drop the (possibly forming) last bar and return only closed candles.

    The signal is always evaluated on the last row of the returned frame, i.e.
    the **second-to-last** bar of the fetched data -- never the live candle.
    """
    if len(df) < 2:
        raise BridgeError(
            "Need at least 2 candles (one forming + one closed) to evaluate a signal."
        )
    return df.iloc[:-1].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Signal generation (reuses presets/strategies/indicators -- no duplication)
# ---------------------------------------------------------------------------
def _clean_float(value: object) -> Optional[float]:
    """JSON-safe float: NaN/inf/None -> None."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _classify_signal(sig: int, raw_reason: str) -> tuple[str, str]:
    """Long-only mapping: only a fresh long entry (signal == 1) is a BUY alert.

    A bearish flip/breakout is reported as NONE with an ``_ignored_long_only``
    reason so the diagnostics still explain *why* there was no entry.
    """
    if sig == 1:
        return "BUY", raw_reason or "long_entry"
    if sig == -1 and raw_reason:
        return "NONE", f"{raw_reason}_ignored_long_only"
    return "NONE", raw_reason or "no_entry"


def humanize_reason(
    reason: str, signal_type: str, family: str, regime: str = "unknown"
) -> str:
    """A plain-English explanation of a signal/no-signal for the UI."""
    if signal_type == "BUY":
        if family == "supertrend":
            return "BUY: latest closed H4 candle produced a fresh bullish SuperTrend flip."
        if family == "donchian":
            return "BUY: latest closed H1 candle broke above the Donchian breakout level."
        return "Fresh long entry signal on the latest closed candle."
    if family == "supertrend":
        if regime == "bearish":
            return (
                "No entry: SuperTrend regime is bearish on the latest closed H4 "
                "candle. The strategy waits for a fresh bullish flip."
            )
        if regime == "bullish":
            return (
                "No entry: SuperTrend regime is already bullish, but there is no "
                "fresh flip on the latest closed H4 candle. The strategy does not "
                "repeat entries."
            )
        return (
            "No entry: latest closed H4 candle did not produce a fresh bullish "
            "SuperTrend flip."
        )
    if family == "donchian":
        return (
            "No entry: latest closed H1 candle did not break the Donchian "
            "breakout level."
        )
    if "ignored_long_only" in reason:
        return "Bearish signal ignored (long-only: no entry)."
    return "No new entry on the latest closed candle."


def next_long_condition(family: str) -> str:
    """The human-readable condition needed for the next BUY signal."""
    if family == "supertrend":
        return SUPER_TREND_NEXT_BUY_CONDITION
    if family == "donchian":
        return DONCHIAN_NEXT_BUY_CONDITION
    return "A fresh long entry condition on a closed candle."


def build_buy_zone_diagnostics(
    *,
    family: str,
    regime: str,
    close_price: Optional[float],
    atr_value: Optional[float],
    supertrend_value: Optional[float] = None,
    donchian_high: Optional[float] = None,
) -> dict:
    """Describe the latest closed candle's distance to the next BUY reference.

    The values are diagnostics only. In particular, a SuperTrend line is the
    current reference boundary, not a guaranteed future trigger price.
    """
    level = supertrend_value if family == "supertrend" else donchian_high
    if close_price is None or level is None:
        relation = "unknown"
        distance = None
    elif family == "supertrend":
        if math.isclose(close_price, level, rel_tol=1e-9, abs_tol=1e-9):
            relation = "at_buy_zone"
        elif close_price < level:
            relation = "below_buy_zone"
        else:
            relation = "above_buy_zone"
        distance = max(level - close_price, 0.0)
    elif family == "donchian":
        relation = "above_buy_zone" if close_price >= level else "below_buy_zone"
        distance = max(level - close_price, 0.0)
    else:
        relation = "unknown"
        distance = None

    distance_atr = (
        _clean_float(distance / atr_value)
        if distance is not None and atr_value is not None and atr_value > 0
        else None
    )
    distance_pct = (
        _clean_float(distance / close_price * 100.0)
        if distance is not None and close_price not in (None, 0)
        else None
    )
    buy_zone_level = (
        level
        if family != "supertrend" or regime in {"bearish", "neutral"}
        else None
    )
    return {
        "next_buy_condition": next_long_condition(family),
        "buy_zone_level": _clean_float(buy_zone_level),
        "distance_to_buy_zone_price": _clean_float(distance),
        "distance_to_buy_zone_atr": distance_atr,
        "distance_to_buy_zone_pct": distance_pct,
        "buy_zone_relation": relation,
    }


def empty_market_context() -> dict:
    """A market context with everything unknown (used when MT5 specs are absent)."""
    return {
        "account_equity": None,
        "contract_size": None,
        "point_value": None,
        "lot_step": None,
        "spread_points": None,
    }


def read_market_context(mt5, symbol: str) -> dict:  # type: ignore[no-untyped-def]
    """Read account equity + symbol specs from MT5, read-only and best-effort.

    Never places, modifies or closes anything: it only reads ``account_info``
    (for equity) and ``symbol_info`` (for contract size / lot step / spread).
    Any missing field stays ``None`` and the caller falls back to config values.
    """
    context = empty_market_context()

    account_info = getattr(mt5, "account_info", None)
    if callable(account_info):
        try:
            info = account_info()
        except Exception:  # pragma: no cover - defensive, broker-dependent
            info = None
        if info is not None:
            context["account_equity"] = _clean_float(getattr(info, "equity", None))

    symbol_info = getattr(mt5, "symbol_info", None)
    if callable(symbol_info):
        try:
            spec = symbol_info(symbol)
        except Exception:  # pragma: no cover - defensive, broker-dependent
            spec = None
        if spec is not None:
            contract_size = _clean_float(getattr(spec, "trade_contract_size", None))
            point = _clean_float(getattr(spec, "point", None))
            context["contract_size"] = contract_size
            context["lot_step"] = _clean_float(getattr(spec, "volume_step", None))
            context["spread_points"] = _clean_float(getattr(spec, "spread", None))
            if contract_size is not None and point is not None:
                context["point_value"] = _clean_float(contract_size * point)
    return context


def _round_down_to_step(value: float, step: float) -> float:
    """Round ``value`` down to the nearest ``step`` (e.g. a broker lot step)."""
    if step <= 0:
        return float(value)
    steps = math.floor(value / step + 1e-9)
    text = f"{step:.10f}".rstrip("0")
    decimals = len(text.split(".")[1]) if "." in text else 0
    return round(steps * step, decimals)


# ---------------------------------------------------------------------------
# Per-candle diagnostics (computed once, reused by the signal + recent checks)
# ---------------------------------------------------------------------------
def compute_diagnostics(config: dict, closed_df: pd.DataFrame) -> pd.DataFrame:
    """Build a per-closed-candle diagnostics frame for a config.

    Reuses :func:`presets.generate_signals` and :mod:`indicators` (no duplicated
    strategy/indicator logic) and adds the strategy-state columns the UI needs:
    ``regime`` plus SuperTrend (D) or Donchian (C) levels. The latest signal and
    the recent-checks table both slice rows from this single frame.
    """
    preset = presets.get_preset(config["strategy_id"])
    strategy_params = dict(config.get("strategy_parameters", {}))

    signals = presets.generate_signals(preset, closed_df, strategy_params)
    stop_period = int(
        config.get("stop_atr_period") or presets.stop_atr_period(preset, strategy_params)
    )
    atr_series = indicators.atr(closed_df, stop_period)

    diag = pd.DataFrame(
        {
            "datetime": closed_df["datetime"].to_numpy(),
            "open": closed_df["open"].to_numpy(),
            "high": closed_df["high"].to_numpy(),
            "low": closed_df["low"].to_numpy(),
            "close": closed_df["close"].to_numpy(),
            "atr": atr_series.to_numpy(),
            "signal": signals["signal"].to_numpy(),
            "signal_reason": signals["signal_reason"].to_numpy(),
        }
    )
    if "spread" in closed_df.columns:
        diag["spread"] = closed_df["spread"].to_numpy()

    if preset.family == "supertrend":
        _add_supertrend_state(diag, closed_df, preset, strategy_params)
    elif preset.family == "donchian":
        _add_donchian_state(diag, closed_df, preset, strategy_params)
    else:  # pragma: no cover - defensive; validate_config blocks other families
        diag["regime"] = "unknown"
    return diag


def _add_supertrend_state(
    diag: pd.DataFrame, closed_df: pd.DataFrame, preset, strategy_params: dict
) -> None:
    """Attach SuperTrend value/regime/distance columns (finalist D)."""
    st = indicators.supertrend(
        closed_df,
        atr_period=int(strategy_params.get("atr_period", preset.defaults["atr_period"])),
        multiplier=float(strategy_params.get("multiplier", preset.defaults["multiplier"])),
    )
    direction = st["direction"].to_numpy()
    diag["supertrend"] = st["supertrend"].to_numpy()
    diag["supertrend_distance_atr"] = (diag["close"] - diag["supertrend"]) / diag["atr"]
    regime = np.where(direction == 1, "bullish", np.where(direction == -1, "bearish", "unknown"))
    diag["regime"] = regime.astype(object)


def _add_donchian_state(
    diag: pd.DataFrame, closed_df: pd.DataFrame, preset, strategy_params: dict
) -> None:
    """Attach Donchian high/low/position/regime columns (finalist C).

    The channel is the *previous* bar's rolling high/low (the breakout level the
    current close must clear), matching :func:`strategies.donchian_breakout_strategy`.
    """
    channel = indicators.donchian_channel(
        closed_df,
        lookback=int(strategy_params.get("lookback", preset.defaults["lookback"])),
    )
    prev_upper = channel["upper"].shift(1).to_numpy()
    prev_lower = channel["lower"].shift(1).to_numpy()
    close = diag["close"].to_numpy()

    diag["donchian_high"] = prev_upper
    diag["donchian_low"] = prev_lower
    width = prev_upper - prev_lower
    with np.errstate(invalid="ignore", divide="ignore"):
        position = np.where(width > 0, (close - prev_lower) / width, np.nan)
    diag["donchian_position"] = position

    known = ~(np.isnan(prev_upper) | np.isnan(prev_lower))
    regime = np.full(len(diag), "unknown", dtype=object)
    regime[known & (close > prev_upper)] = "bullish"
    regime[known & (close < prev_lower)] = "bearish"
    regime[known & (close <= prev_upper) & (close >= prev_lower)] = "neutral"
    diag["regime"] = regime


def _bars_since_last_long(diag: pd.DataFrame) -> Optional[int]:
    """Closed candles since the most recent long signal (0 == this candle)."""
    longs = np.where(diag["signal"].to_numpy() == 1)[0]
    if len(longs) == 0:
        return None
    return int(len(diag) - 1 - longs[-1])


def _resolve_equity(context: dict, risk_params: dict) -> tuple[Optional[float], str]:
    """Account equity reference + its source (MT5 equity beats config equity)."""
    equity = _clean_float(context.get("account_equity"))
    if equity is not None:
        return equity, "mt5_account_equity"
    config_equity = _clean_float(risk_params.get("initial_equity"))
    if config_equity is not None:
        return config_equity, "config_initial_equity"
    return None, "unavailable"


def _resolve_spread(context: dict, last_row: pd.Series) -> Optional[float]:
    """Spread points from MT5 symbol_info, else the candle's spread, else None."""
    spread = _clean_float(context.get("spread_points"))
    if spread is not None:
        return spread
    if "spread" in last_row.index:
        return _clean_float(last_row["spread"])
    return None


# ---------------------------------------------------------------------------
# Trading plan (signal-only REFERENCE -- never an order instruction)
# ---------------------------------------------------------------------------
_BUY_NOTE = (
    "Signal-only reference. No order is sent, modified or closed. "
    "reference_entry_price is the latest closed price used as a conservative "
    "reference — the live next-bar open / actual fill is not guaranteed."
)
_NONE_NOTE = (
    "Signal-only reference. No new entry on the latest closed candle; "
    "no order is sent."
)


def build_trading_plan(
    *,
    signal_type: str,
    family: str,
    reason: str,
    close_price: Optional[float],
    atr_value: Optional[float],
    regime: str,
    initial_stop_loss_atr: Optional[float],
    trailing_stop_atr: Optional[float],
    take_profit_atr: Optional[float],
    risk_percent: Optional[float],
    account_equity: Optional[float],
    account_equity_source: str,
    contract_size: Optional[float],
    point_value: Optional[float],
    lot_step: Optional[float],
) -> dict:
    """Build the reference trading plan for a BUY or a NONE.

    Everything is a *reference* for a signal-only workflow; no order is ever
    placed. For NONE there is deliberately no entry/stop (only an optional
    informational trailing reference when the regime is already bullish).
    """
    contract = contract_size if contract_size is not None else DEFAULT_CONTRACT_SIZE
    step = lot_step if (lot_step and lot_step > 0) else DEFAULT_LOT_STEP

    can_trail = (
        trailing_stop_atr is not None and close_price is not None and atr_value is not None
    )
    trailing_reference = (
        _clean_float(close_price - trailing_stop_atr * atr_value) if can_trail else None
    )

    plan = {
        "reference_entry_type": None,
        "reference_entry_price": None,
        "initial_stop_price": None,
        "trailing_stop_reference": None,
        "take_profit_price": None,
        "risk_per_unit": None,
        "risk_percent": _clean_float(risk_percent),
        "account_equity_reference": _clean_float(account_equity),
        "account_equity_source": account_equity_source,
        "risk_amount": None,
        "suggested_lot": None,
        "contract_size": _clean_float(contract),
        "point_value": _clean_float(point_value),
        "lot_step": _clean_float(step),
        "reason_human": humanize_reason(reason, signal_type, family, regime),
        "next_buy_condition": next_long_condition(family),
        "notes": _NONE_NOTE,
    }

    if signal_type != "BUY":
        # Only surface a trailing reference when already in a bullish regime; never
        # invent an entry price when there is no entry.
        plan["trailing_stop_reference"] = trailing_reference if regime == "bullish" else None
        plan["next_condition"] = plan["next_buy_condition"]
        return plan

    entry = close_price
    initial_stop = (
        _clean_float(entry - initial_stop_loss_atr * atr_value)
        if (entry is not None and initial_stop_loss_atr is not None and atr_value is not None)
        else None
    )
    take_profit = (
        _clean_float(entry + take_profit_atr * atr_value)
        if (entry is not None and take_profit_atr is not None and atr_value is not None)
        else None
    )
    risk_per_unit = (
        _clean_float(entry - initial_stop)
        if (entry is not None and initial_stop is not None)
        else None
    )
    risk_amount = (
        _clean_float(account_equity * risk_percent / 100.0)
        if (account_equity is not None and risk_percent is not None)
        else None
    )
    suggested_lot = None
    if risk_amount is not None and risk_per_unit and risk_per_unit > 0 and contract > 0:
        raw_lot = risk_amount / (risk_per_unit * contract)
        suggested_lot = _round_down_to_step(raw_lot, step)

    plan.update(
        {
            "reference_entry_type": REFERENCE_ENTRY_TYPE,
            "reference_entry_price": entry,
            "initial_stop_price": initial_stop,
            "trailing_stop_reference": trailing_reference,
            "take_profit_price": take_profit,
            "risk_per_unit": risk_per_unit,
            "risk_amount": risk_amount,
            "suggested_lot": suggested_lot,
            "notes": _BUY_NOTE,
        }
    )
    return plan


# ---------------------------------------------------------------------------
# Signal record (latest closed candle) + recent-candle diagnostics
# ---------------------------------------------------------------------------
def build_signal_record(
    config: dict,
    closed_df: pd.DataFrame,
    *,
    symbol: str,
    generated_at: Optional[datetime] = None,
    market_context: Optional[dict] = None,
) -> dict:
    """Compute the enriched v1.7.3 signal record for the latest closed candle.

    ``closed_df`` must already exclude the forming candle
    (see :func:`select_closed_candles`). Signal generation is delegated to
    :func:`presets.generate_signals` so it stays identical to the backtester.
    The record stays signal-only: ``execution_enabled`` is always ``False`` and
    the ``trading_plan`` is a labelled reference, never an order.
    """
    assert_signal_only()
    diag = compute_diagnostics(config, closed_df)
    return _record_from_diagnostics(
        config,
        diag,
        symbol=symbol,
        generated_at=generated_at,
        market_context=market_context or empty_market_context(),
    )


def _record_from_diagnostics(
    config: dict,
    diag: pd.DataFrame,
    *,
    symbol: str,
    generated_at: Optional[datetime],
    market_context: dict,
) -> dict:
    preset = presets.get_preset(config["strategy_id"])
    family = preset.family
    timeframe = str(config["timeframe"]).upper()
    exit_params = dict(config.get("exit_parameters", {}))
    risk_params = dict(config.get("risk_parameters", {}))

    last = diag.iloc[-1]
    sig = int(last["signal"])
    raw_reason = str(last["signal_reason"] or "")
    signal_type, reason = _classify_signal(sig, raw_reason)
    signal_time = pd.Timestamp(last["datetime"])

    close_price = _clean_float(last["close"])
    atr_value = _clean_float(last["atr"])
    regime = str(last["regime"]) if "regime" in diag.columns else "unknown"
    supertrend_value = (
        _clean_float(last["supertrend"]) if "supertrend" in diag.columns else None
    )
    donchian_high = (
        _clean_float(last["donchian_high"]) if "donchian_high" in diag.columns else None
    )
    buy_zone = build_buy_zone_diagnostics(
        family=family,
        regime=regime,
        close_price=close_price,
        atr_value=atr_value,
        supertrend_value=supertrend_value,
        donchian_high=donchian_high,
    )

    # C uses stop_loss_atr; D uses initial_stop_loss_atr + trailing_stop_atr.
    initial_stop_loss_atr = _clean_float(
        exit_params.get("initial_stop_loss_atr", exit_params.get("stop_loss_atr"))
    )
    trailing_stop_atr = _clean_float(exit_params.get("trailing_stop_atr"))
    take_profit_atr = _clean_float(exit_params.get("take_profit_atr"))
    risk_percent = _clean_float(risk_params.get("risk_percent"))

    equity, equity_source = _resolve_equity(market_context, risk_params)

    previous_regime = (
        str(diag["regime"].iloc[-2])
        if (len(diag) >= 2 and "regime" in diag.columns)
        else None
    )
    strategy_state = {
        "strategy_regime": regime,
        "previous_strategy_regime": previous_regime,
        "is_new_long_signal": signal_type == "BUY",
        "bars_since_last_long_signal": _bars_since_last_long(diag),
        "supertrend_value": supertrend_value,
        "supertrend_distance_atr": (
            _clean_float(last["supertrend_distance_atr"])
            if "supertrend_distance_atr" in diag.columns
            else None
        ),
        "donchian_high": donchian_high,
        "donchian_low": _clean_float(last["donchian_low"]) if "donchian_low" in diag.columns else None,
        "donchian_position": (
            _clean_float(last["donchian_position"]) if "donchian_position" in diag.columns else None
        ),
        **buy_zone,
    }

    market_snapshot = {
        "close_price": close_price,
        "open_price": _clean_float(last["open"]),
        "high_price": _clean_float(last["high"]),
        "low_price": _clean_float(last["low"]),
        "atr_value": atr_value,
        "spread_points": _resolve_spread(market_context, last),
        "latest_closed_candle_time": signal_time.isoformat(),
        "previous_closed_candle_time": (
            pd.Timestamp(diag["datetime"].iloc[-2]).isoformat() if len(diag) >= 2 else None
        ),
    }

    trading_plan = build_trading_plan(
        signal_type=signal_type,
        family=family,
        reason=reason,
        close_price=close_price,
        atr_value=atr_value,
        regime=regime,
        initial_stop_loss_atr=initial_stop_loss_atr,
        trailing_stop_atr=trailing_stop_atr,
        take_profit_atr=take_profit_atr,
        risk_percent=risk_percent,
        account_equity=equity,
        account_equity_source=equity_source,
        contract_size=market_context.get("contract_size"),
        point_value=market_context.get("point_value"),
        lot_step=market_context.get("lot_step"),
    )

    generated_at = generated_at or datetime.now(timezone.utc)
    signal_id = (
        f"{config['strategy_id']}_{symbol}_{timeframe}_"
        f"{signal_time.strftime('%Y%m%dT%H%M%S')}"
    )

    return {
        # identity
        "signal_id": signal_id,
        "generated_at": generated_at.isoformat(),
        "symbol": symbol,
        "timeframe": timeframe,
        "strategy_id": config["strategy_id"],
        "signal_time": signal_time.isoformat(),
        "signal_type": signal_type,
        "reason": reason,
        "reason_human": trading_plan["reason_human"],
        "status": SIGNAL_STATUS,
        "execution_enabled": EXECUTION_ENABLED,  # always False in v1.7.x
        # flat fields kept for back-compat with earlier consumers + CSV history
        "close_price": close_price,
        "atr_value": atr_value,
        "suggested_entry_reference": SUGGESTED_ENTRY_REFERENCE,
        "risk_percent": risk_percent,
        "initial_stop_loss_atr": initial_stop_loss_atr,
        "trailing_stop_atr": trailing_stop_atr,
        "take_profit_atr": take_profit_atr,
        "strategy_regime": regime,
        # v1.7.3 flat diagnostics (also used by CSV history consumers)
        "next_buy_condition": buy_zone["next_buy_condition"],
        "buy_zone_level": buy_zone["buy_zone_level"],
        "distance_to_buy_zone_price": buy_zone["distance_to_buy_zone_price"],
        "distance_to_buy_zone_atr": buy_zone["distance_to_buy_zone_atr"],
        "distance_to_buy_zone_pct": buy_zone["distance_to_buy_zone_pct"],
        "buy_zone_relation": buy_zone["buy_zone_relation"],
        # enriched nested objects
        "market_snapshot": market_snapshot,
        "strategy_state": strategy_state,
        "trading_plan": trading_plan,
    }


def build_recent_checks(
    config: dict,
    closed_df: pd.DataFrame,
    *,
    limit: int = DEFAULT_RECENT_LIMIT,
    market_context: Optional[dict] = None,
) -> list[dict]:
    """Diagnostics for the latest ``limit`` closed candles (newest first).

    This is a *display* aid: it never emits an official signal and never places
    an order. It reuses the same :func:`compute_diagnostics` frame as the latest
    signal, so the two can never disagree.
    """
    limit = max(1, min(int(limit), MAX_RECENT_LIMIT))
    preset = presets.get_preset(config["strategy_id"])
    family = preset.family
    exit_params = dict(config.get("exit_parameters", {}))
    initial_stop_loss_atr = _clean_float(
        exit_params.get("initial_stop_loss_atr", exit_params.get("stop_loss_atr"))
    )
    trailing_stop_atr = _clean_float(exit_params.get("trailing_stop_atr"))

    diag = compute_diagnostics(config, closed_df)
    tail = diag.iloc[-limit:]

    rows: list[dict] = []
    for _, row in tail.iterrows():
        sig = int(row["signal"])
        raw_reason = str(row["signal_reason"] or "")
        signal_type, reason = _classify_signal(sig, raw_reason)
        close_price = _clean_float(row["close"])
        atr_value = _clean_float(row["atr"])
        regime = str(row["regime"]) if "regime" in diag.columns else "unknown"
        supertrend_value = (
            _clean_float(row["supertrend"]) if "supertrend" in diag.columns else None
        )
        donchian_high = (
            _clean_float(row["donchian_high"]) if "donchian_high" in diag.columns else None
        )
        buy_zone = build_buy_zone_diagnostics(
            family=family,
            regime=regime,
            close_price=close_price,
            atr_value=atr_value,
            supertrend_value=supertrend_value,
            donchian_high=donchian_high,
        )

        trailing_reference = (
            _clean_float(close_price - trailing_stop_atr * atr_value)
            if (trailing_stop_atr is not None and close_price is not None and atr_value is not None)
            else None
        )
        initial_stop_reference = (
            _clean_float(close_price - initial_stop_loss_atr * atr_value)
            if (
                signal_type == "BUY"
                and initial_stop_loss_atr is not None
                and close_price is not None
                and atr_value is not None
            )
            else None
        )

        rows.append(
            {
                "signal_time": pd.Timestamp(row["datetime"]).isoformat(),
                "close_price": close_price,
                "atr_value": atr_value,
                "strategy_regime": regime,
                "is_long_signal": signal_type == "BUY",
                "signal_type": signal_type,
                "reason": reason,
                "reason_human": humanize_reason(
                    reason, signal_type, family, regime
                ),
                "supertrend_value": supertrend_value,
                "donchian_high": donchian_high,
                "donchian_low": _clean_float(row["donchian_low"]) if "donchian_low" in diag.columns else None,
                **buy_zone,
                "initial_stop_reference": initial_stop_reference,
                "trailing_stop_reference": trailing_reference,
                "execution_enabled": EXECUTION_ENABLED,  # always False
            }
        )
    rows.reverse()  # newest first
    return rows


def flatten_signal_for_csv(signal: dict) -> dict:
    """Flatten an enriched signal record to the :data:`SIGNAL_CSV_FIELDS` columns.

    Tolerates a minimal record (no nested ``trading_plan``/``strategy_state``):
    missing references simply flatten to ``None``.
    """
    plan = signal.get("trading_plan") or {}
    state = signal.get("strategy_state") or {}
    risk_percent = signal.get("risk_percent")
    if risk_percent is None:
        risk_percent = plan.get("risk_percent")
    return {
        "signal_id": signal.get("signal_id"),
        "generated_at": signal.get("generated_at"),
        "signal_time": signal.get("signal_time"),
        "symbol": signal.get("symbol"),
        "timeframe": signal.get("timeframe"),
        "strategy_id": signal.get("strategy_id"),
        "signal_type": signal.get("signal_type"),
        "reason": signal.get("reason"),
        "reason_human": signal.get("reason_human") or plan.get("reason_human"),
        "close_price": signal.get("close_price"),
        "atr_value": signal.get("atr_value"),
        "strategy_regime": signal.get("strategy_regime") or state.get("strategy_regime"),
        "buy_zone_level": signal.get("buy_zone_level", state.get("buy_zone_level")),
        "distance_to_buy_zone_price": signal.get(
            "distance_to_buy_zone_price", state.get("distance_to_buy_zone_price")
        ),
        "distance_to_buy_zone_atr": signal.get(
            "distance_to_buy_zone_atr", state.get("distance_to_buy_zone_atr")
        ),
        "distance_to_buy_zone_pct": signal.get(
            "distance_to_buy_zone_pct", state.get("distance_to_buy_zone_pct")
        ),
        "buy_zone_relation": signal.get(
            "buy_zone_relation", state.get("buy_zone_relation")
        ),
        "reference_entry_price": plan.get("reference_entry_price"),
        "initial_stop_price": plan.get("initial_stop_price"),
        "trailing_stop_reference": plan.get("trailing_stop_reference"),
        "take_profit_price": plan.get("take_profit_price"),
        "risk_percent": risk_percent,
        "suggested_lot": plan.get("suggested_lot"),
        "execution_enabled": signal.get("execution_enabled"),
    }


# ---------------------------------------------------------------------------
# Orchestration (one check). MT5 access is injected for testability.
# ---------------------------------------------------------------------------
# fetch_ohlc_fn(symbol, timeframe, bars) -> closed+forming OHLC DataFrame.
FetchOhlcFn = Callable[[str, str, int], pd.DataFrame]


def make_fetch_ohlc_fn(mt5) -> FetchOhlcFn:  # type: ignore[no-untyped-def]
    """Bind a live MT5 module into a ``fetch_ohlc_fn`` for :func:`run_once`."""

    def _fetch(symbol: str, timeframe: str, bars: int) -> pd.DataFrame:
        return rates_to_dataframe(fetch_rates(mt5, symbol, timeframe, bars))

    return _fetch


def run_once(
    config: dict,
    store,
    *,
    symbol: str,
    fetch_ohlc_fn: FetchOhlcFn,
    bars: int = DEFAULT_BARS,
    generated_at: Optional[datetime] = None,
    recent_limit: int = DEFAULT_RECENT_LIMIT,
    market_context: Optional[dict] = None,
) -> Optional[dict]:
    """Run one signal check.

    Always refreshes the recent-candle diagnostics (so the UI can show what
    happened over the last several candles), then emits the official signal for
    the latest closed candle. Returns the emitted signal record, or ``None`` when
    that candle has already been processed (one signal per candle -- no
    duplicates). Refreshing diagnostics never emits a second signal.
    """
    assert_signal_only()

    timeframe = str(config["timeframe"]).upper()
    strategy_id = config["strategy_id"]
    generated_at = generated_at or datetime.now(timezone.utc)

    df = fetch_ohlc_fn(symbol, timeframe, bars)
    closed = select_closed_candles(df)
    signal_time = pd.Timestamp(closed["datetime"].iloc[-1])

    checks = build_recent_checks(
        config, closed, limit=recent_limit, market_context=market_context
    )
    store.write_recent_checks(
        {
            "generated_at": generated_at.isoformat(),
            "symbol": symbol,
            "timeframe": timeframe,
            "strategy_id": strategy_id,
            "limit": len(checks),
            "checks": checks,
        }
    )

    key = store.make_key(strategy_id, symbol, timeframe)
    if store.already_processed(key, signal_time):
        return None

    record = build_signal_record(
        config,
        closed,
        symbol=symbol,
        generated_at=generated_at,
        market_context=market_context,
    )
    store.record(key, record)
    return record
