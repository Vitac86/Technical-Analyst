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

# Stable field order for the emitted signal record (also the signals.csv header).
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


def build_signal_record(
    config: dict,
    closed_df: pd.DataFrame,
    *,
    symbol: str,
    generated_at: Optional[datetime] = None,
) -> dict:
    """Compute the v1.7 signal record for the latest closed candle.

    ``closed_df`` must already exclude the forming candle
    (see :func:`select_closed_candles`). Signal generation is delegated to
    :func:`app.strategy_lab.presets.generate_signals` so it is identical to the
    Strategy Lab backtester.
    """
    assert_signal_only()

    strategy_id = config["strategy_id"]
    timeframe = str(config["timeframe"]).upper()
    preset = presets.get_preset(strategy_id)

    strategy_params = dict(config.get("strategy_parameters", {}))
    exit_params = dict(config.get("exit_parameters", {}))
    risk_params = dict(config.get("risk_parameters", {}))

    signals = presets.generate_signals(preset, closed_df, strategy_params)
    stop_period = int(
        config.get("stop_atr_period") or presets.stop_atr_period(preset, strategy_params)
    )
    atr_series = indicators.atr(closed_df, stop_period)

    sig = int(signals["signal"].iloc[-1])
    raw_reason = str(signals["signal_reason"].iloc[-1] or "")
    signal_time = pd.Timestamp(closed_df["datetime"].iloc[-1])

    # Long-only: only a fresh long entry (signal == 1) becomes a BUY alert.
    if sig == 1:
        signal_type = "BUY"
        reason = raw_reason or "long_entry"
    else:
        signal_type = "NONE"
        if sig == -1 and raw_reason:
            reason = f"{raw_reason}_ignored_long_only"
        else:
            reason = raw_reason or "no_entry"

    # Map exit params to the output schema (C uses stop_loss_atr; D uses
    # initial_stop_loss_atr + trailing_stop_atr).
    initial_stop_loss_atr = exit_params.get(
        "initial_stop_loss_atr", exit_params.get("stop_loss_atr")
    )

    generated_at = generated_at or datetime.now(timezone.utc)
    signal_id = (
        f"{strategy_id}_{symbol}_{timeframe}_{signal_time.strftime('%Y%m%dT%H%M%S')}"
    )

    return {
        "signal_id": signal_id,
        "generated_at": generated_at.isoformat(),
        "symbol": symbol,
        "timeframe": timeframe,
        "strategy_id": strategy_id,
        "signal_time": signal_time.isoformat(),
        "signal_type": signal_type,
        "reason": reason,
        "close_price": _clean_float(closed_df["close"].iloc[-1]),
        "atr_value": _clean_float(atr_series.iloc[-1]),
        "suggested_entry_reference": SUGGESTED_ENTRY_REFERENCE,
        "risk_percent": _clean_float(risk_params.get("risk_percent")),
        "initial_stop_loss_atr": _clean_float(initial_stop_loss_atr),
        "trailing_stop_atr": _clean_float(exit_params.get("trailing_stop_atr")),
        "take_profit_atr": _clean_float(exit_params.get("take_profit_atr")),
        "status": SIGNAL_STATUS,
        "execution_enabled": EXECUTION_ENABLED,  # always False in v1.7
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
) -> Optional[dict]:
    """Run one signal check.

    Returns the emitted signal record, or ``None`` when the latest closed candle
    has already been processed (one signal per candle -- no duplicates).
    """
    assert_signal_only()

    timeframe = str(config["timeframe"]).upper()
    strategy_id = config["strategy_id"]

    df = fetch_ohlc_fn(symbol, timeframe, bars)
    closed = select_closed_candles(df)
    signal_time = pd.Timestamp(closed["datetime"].iloc[-1])

    key = store.make_key(strategy_id, symbol, timeframe)
    if store.already_processed(key, signal_time):
        return None

    record = build_signal_record(
        config, closed, symbol=symbol, generated_at=generated_at
    )
    store.record(key, record)
    return record
