"""Strategy Lab v1.8: MetaTrader 5 **demo execution robot** (separate from the bridge).

This module is the execution-side counterpart of the signal-only bridge
(:mod:`app.strategy_lab.mt5_signal_bridge`). The signal-only bridge is left
completely intact: it is never imported as a "trading component" -- the robot
only **reuses its read-only research helpers** (config loading/validation, MT5
init/shutdown, rates fetching, the closed-candle rule and the rule-based signal
record) so the live entry stays byte-for-byte aligned with the backtester.

Hard safety principles (v1.8):

    * **Demo only.** Orders are sent only when the connected MT5 account is
      *detected as a demo account*. A live or unknown account is always refused.
    * **Dry-run is the default.** Nothing is ever sent unless
      ``execution_enabled=True`` **and** every demo safety gate passes.
    * **Long-only.** Only a fresh D SuperTrend H4 BUY is ever opened. There is no
      SELL / SHORT entry code path anywhere in this module.
    * **Never close.** v1.8 only opens a BUY and trails its stop *upward*. It
      never closes a position and never moves a stop downward.
    * **No credentials.** It attaches to the already-running, already-logged-in
      local terminal. It never handles a login/password.

Three conceptual modes:

    * ``dry_run``        -- default; computes what *would* happen, sends nothing.
    * ``demo_execution`` -- sends orders, but only if all demo gates pass.
    * ``live_execution`` -- not implemented in v1.8; always refused.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional

import pandas as pd

try:  # package import
    from . import mt5_signal_bridge as bridge
    from .execution_store import ExecutionStore
except ImportError:  # script import (``python .../run_mt5_execution_robot.py``)
    import mt5_signal_bridge as bridge  # type: ignore[no-redef]
    from execution_store import ExecutionStore  # type: ignore[no-redef]


# ---------------------------------------------------------------------------
# Version + the single supported strategy (D)
# ---------------------------------------------------------------------------
EXECUTION_ROBOT_VERSION: str = "1.8"

# v1.8 supports ONLY finalist D (SuperTrend H4, long-only, ATR trailing, risk%).
SUPPORTED_STRATEGY_ID: str = "D_supertrend_h4_trailing_risk"
SUPPORTED_TIMEFRAME: str = "H4"
DEFAULT_SYMBOL: str = "XAUUSDrfd"

UNSUPPORTED_CONFIG_MESSAGE: str = (
    "Execution robot v1.8 supports only D SuperTrend H4. "
    "C remains research/signal-only."
)

# Order defaults (overridable per request).
DEFAULT_MAGIC: int = 170801
DEFAULT_DEVIATION: int = 50
ORDER_COMMENT: str = "TA D SuperTrend H4 v1.8"
TRAILING_COMMENT: str = "TA trailing update"

# Fallback contract size when MT5 symbol specs / config are unavailable.
DEFAULT_CONTRACT_SIZE: float = 100.0  # XAUUSD: 100 oz per 1.00 lot.
DEFAULT_VOLUME_STEP: float = 0.01

# Modes.
MODE_DRY_RUN: str = "dry_run"
MODE_DEMO_EXECUTION: str = "demo_execution"
MODE_REFUSED: str = "refused"

# Intended actions.
ACTION_NO_ACTION: str = "no_action"
ACTION_WOULD_OPEN_BUY: str = "would_open_buy"
ACTION_OPENED_BUY: str = "opened_buy"
ACTION_WOULD_UPDATE_TRAILING: str = "would_update_trailing_sl"
ACTION_UPDATED_TRAILING: str = "updated_trailing_sl"
ACTION_REFUSED: str = "refused"

# Refusal reasons (machine-readable; surfaced verbatim in the UI).
REFUSE_ACCOUNT_NOT_DEMO: str = "account_is_not_demo_live_or_unknown"
REFUSE_EXECUTION_NOT_ENABLED: str = "execution_not_enabled"
REFUSE_DEMO_ONLY_REQUIRED: str = "demo_only_flag_required"
REFUSE_NOT_CONFIRMED: str = "demo_execution_not_confirmed"
REFUSE_LOT_BELOW_MIN: str = "lot_below_minimum"
REFUSE_MARGIN_INSUFFICIENT: str = "margin_insufficient"
REFUSE_INVALID_SIZING: str = "invalid_lot_sizing"
REFUSE_SEND_FAILED: str = "order_send_failed"
REFUSE_LIVE_NOT_SUPPORTED: str = "live_execution_not_supported_in_v1_8"

# Notes (informational; never block on their own).
NOTE_DUPLICATE: str = "duplicate_signal_time_already_processed"
NOTE_SETUP_ENDED: str = "setup_ended_but_no_close_in_v1_8"
NOTE_ONE_POSITION: str = "existing_position_one_position_only"

# Sizing status values.
SIZING_OK: str = "ok"
SIZING_LOT_BELOW_MIN: str = "lot_below_min"
SIZING_MARGIN_INSUFFICIENT: str = "margin_insufficient"
SIZING_INVALID: str = "invalid_sizing"


class ExecutionError(RuntimeError):
    """A user-facing execution error (bad/unsupported config, MT5 unavailable)."""


# ---------------------------------------------------------------------------
# Small JSON-safe helpers
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


def round_down_to_step(value: float, step: float) -> float:
    """Round ``value`` down to the nearest broker volume ``step``."""
    if step <= 0:
        return float(value)
    steps = math.floor(value / step + 1e-9)
    text = f"{step:.10f}".rstrip("0")
    decimals = len(text.split(".")[1]) if "." in text else 0
    return round(steps * step, decimals)


# ---------------------------------------------------------------------------
# Config validation (only D in v1.8)
# ---------------------------------------------------------------------------
def validate_execution_config(config: dict) -> None:
    """Validate a config for the v1.8 demo execution robot.

    Reuses :func:`bridge.validate_config` (ML disabled, long-only, supported
    strategy + locked timeframe) and then narrows support to **D only**. Finalist
    C -- which the signal-only bridge still supports -- is refused here with a
    clear message.
    """
    strategy_id = config.get("strategy_id")
    if strategy_id != SUPPORTED_STRATEGY_ID:
        raise ExecutionError(UNSUPPORTED_CONFIG_MESSAGE)
    # Reuse the bridge's contract (ml disabled, long_only, H4) -- no duplication.
    # Bridge refusals (ml enabled, non-long-only, wrong timeframe) are surfaced as
    # ExecutionError so callers handle a single error type.
    try:
        bridge.validate_config(config)
    except bridge.BridgeError as exc:
        raise ExecutionError(str(exc)) from exc
    if str(config.get("direction_mode")) != "long_only":
        raise ExecutionError("direction_mode must be 'long_only' for execution.")


def config_summary(config: dict) -> dict:
    """A small, UI-friendly summary of an execution config."""
    exit_params = dict(config.get("exit_parameters", {}))
    risk_params = dict(config.get("risk_parameters", {}))
    return {
        "strategy_id": config.get("strategy_id"),
        "symbol": config.get("symbol"),
        "timeframe": config.get("timeframe"),
        "direction_mode": config.get("direction_mode"),
        "exit_mode": config.get("exit_mode"),
        "sizing_mode": config.get("sizing_mode"),
        "ml_filter_enabled": bool(config.get("ml_filter_enabled", False)),
        "risk_percent": _clean_float(risk_params.get("risk_percent")),
        "initial_stop_loss_atr": _clean_float(
            exit_params.get("initial_stop_loss_atr")
        ),
        "trailing_stop_atr": _clean_float(exit_params.get("trailing_stop_atr")),
        "take_profit_atr": _clean_float(exit_params.get("take_profit_atr")),
        "is_supported": config.get("strategy_id") == SUPPORTED_STRATEGY_ID,
        "unsupported_reason": (
            None
            if config.get("strategy_id") == SUPPORTED_STRATEGY_ID
            else UNSUPPORTED_CONFIG_MESSAGE
        ),
    }


# ---------------------------------------------------------------------------
# Read-only MT5 inspection (account / symbol / market / position)
# ---------------------------------------------------------------------------
def _trade_mode_label(mt5, trade_mode: object) -> str:  # type: ignore[no-untyped-def]
    """Map an account ``trade_mode`` constant to a human label."""
    demo = getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", 0)
    contest = getattr(mt5, "ACCOUNT_TRADE_MODE_CONTEST", 1)
    real = getattr(mt5, "ACCOUNT_TRADE_MODE_REAL", 2)
    if trade_mode == demo:
        return "demo"
    if trade_mode == contest:
        return "contest"
    if trade_mode == real:
        return "real"
    return "unknown"


def read_account(mt5) -> dict:  # type: ignore[no-untyped-def]
    """Read account identity + equity/margin and detect whether it is a demo.

    ``is_demo`` is *only* true for an explicit demo ``trade_mode``. Anything else
    -- real, contest, or an unreadable account -- is treated as not-demo, so
    execution is refused by default.
    """
    info = None
    account_info = getattr(mt5, "account_info", None)
    if callable(account_info):
        try:
            info = account_info()
        except Exception:  # pragma: no cover - broker-dependent
            info = None

    if info is None:
        return {
            "login": None,
            "server": None,
            "trade_mode": "unknown",
            "is_demo": False,
            "equity": None,
            "free_margin": None,
        }

    trade_mode_raw = getattr(info, "trade_mode", None)
    label = _trade_mode_label(mt5, trade_mode_raw)
    return {
        "login": getattr(info, "login", None),
        "server": getattr(info, "server", None),
        "trade_mode": label,
        "is_demo": label == "demo",
        "equity": _clean_float(getattr(info, "equity", None)),
        "free_margin": _clean_float(getattr(info, "margin_free", None)),
    }


def read_symbol_spec(mt5, symbol: str, config: dict) -> dict:  # type: ignore[no-untyped-def]
    """Read the symbol's volume rules, contract size, filling and stop level."""
    spec = None
    symbol_info = getattr(mt5, "symbol_info", None)
    if callable(symbol_info):
        try:
            spec = symbol_info(symbol)
        except Exception:  # pragma: no cover - broker-dependent
            spec = None

    contract_size = _clean_float(getattr(spec, "trade_contract_size", None))
    if contract_size is None:
        contract_size = _clean_float(config.get("contract_size"))
    if contract_size is None:
        contract_size = DEFAULT_CONTRACT_SIZE

    volume_step = _clean_float(getattr(spec, "volume_step", None)) or DEFAULT_VOLUME_STEP
    return {
        "contract_size": contract_size,
        "volume_min": _clean_float(getattr(spec, "volume_min", None)) or volume_step,
        "volume_max": _clean_float(getattr(spec, "volume_max", None)),
        "volume_step": volume_step,
        "point": _clean_float(getattr(spec, "point", None)),
        "stops_level_points": _clean_float(getattr(spec, "trade_stops_level", None)),
        "filling_mode": getattr(spec, "filling_mode", None),
        "digits": getattr(spec, "digits", None),
    }


def read_market(mt5, symbol: str) -> dict:  # type: ignore[no-untyped-def]
    """Read current bid/ask from a tick snapshot (read-only)."""
    bid = ask = None
    tick_fn = getattr(mt5, "symbol_info_tick", None)
    if callable(tick_fn):
        try:
            tick = tick_fn(symbol)
        except Exception:  # pragma: no cover - broker-dependent
            tick = None
        if tick is not None:
            bid = _clean_float(getattr(tick, "bid", None))
            ask = _clean_float(getattr(tick, "ask", None))
    return {"bid": bid, "ask": ask}


def read_position(mt5, symbol: str, magic: int) -> dict:  # type: ignore[no-untyped-def]
    """Find an existing BUY position for ``symbol``/``magic`` (one-position-only).

    Only a BUY position counts: the robot never opens shorts, so a short position
    (if a user had one from elsewhere) is ignored for the long-only workflow.
    """
    empty = {
        "has_position": False,
        "ticket": None,
        "volume": None,
        "price_open": None,
        "sl": None,
        "tp": None,
    }
    positions_get = getattr(mt5, "positions_get", None)
    if not callable(positions_get):
        return empty
    try:
        positions = positions_get(symbol=symbol)
    except Exception:  # pragma: no cover - broker-dependent
        positions = None
    if not positions:
        return empty

    buy_type = getattr(mt5, "POSITION_TYPE_BUY", 0)
    for pos in positions:
        if getattr(pos, "magic", None) != magic:
            continue
        if getattr(pos, "type", buy_type) != buy_type:
            continue  # never manage a short in v1.8
        return {
            "has_position": True,
            "ticket": getattr(pos, "ticket", None),
            "volume": _clean_float(getattr(pos, "volume", None)),
            "price_open": _clean_float(getattr(pos, "price_open", None)),
            "sl": _clean_float(getattr(pos, "sl", None)),
            "tp": _clean_float(getattr(pos, "tp", None)),
        }
    return empty


def _resolve_filling_mode(mt5, filling_mode: object):  # type: ignore[no-untyped-def]
    """Pick an ``ORDER_FILLING_*`` constant from the symbol's allowed mask.

    Prefers FOK, then IOC, then falls back to RETURN -- whichever the broker
    advertises. Never raises: an unknown mask falls back to IOC.
    """
    fok_flag = getattr(mt5, "SYMBOL_FILLING_FOK", 1)
    ioc_flag = getattr(mt5, "SYMBOL_FILLING_IOC", 2)
    order_fok = getattr(mt5, "ORDER_FILLING_FOK", 0)
    order_ioc = getattr(mt5, "ORDER_FILLING_IOC", 1)
    order_return = getattr(mt5, "ORDER_FILLING_RETURN", 2)

    if isinstance(filling_mode, int):
        if filling_mode & fok_flag:
            return order_fok
        if filling_mode & ioc_flag:
            return order_ioc
        return order_return
    return order_ioc


# ---------------------------------------------------------------------------
# Risk-percent sizing
# ---------------------------------------------------------------------------
def compute_sizing(
    *,
    equity: Optional[float],
    risk_percent: Optional[float],
    entry_price: Optional[float],
    atr_value: Optional[float],
    initial_stop_loss_atr: Optional[float],
    spec: dict,
    free_margin: Optional[float],
    required_margin: Optional[float],
    allow_min_lot_rounding: bool,
) -> dict:
    """Compute a risk-percent lot and return full sizing diagnostics.

    ``required_margin`` is supplied by the caller (via ``mt5.order_calc_margin``)
    so this function stays pure and unit-testable. The returned
    ``sizing_status`` is one of :data:`SIZING_OK`, :data:`SIZING_LOT_BELOW_MIN`,
    :data:`SIZING_MARGIN_INSUFFICIENT` or :data:`SIZING_INVALID`.
    """
    contract_size = spec.get("contract_size") or DEFAULT_CONTRACT_SIZE
    volume_min = spec.get("volume_min") or DEFAULT_VOLUME_STEP
    volume_max = spec.get("volume_max")
    volume_step = spec.get("volume_step") or DEFAULT_VOLUME_STEP

    diagnostics: dict = {
        "equity": _clean_float(equity),
        "risk_percent": _clean_float(risk_percent),
        "risk_amount": None,
        "entry_price": _clean_float(entry_price),
        "initial_stop_price": None,
        "risk_per_unit": None,
        "atr_value": _clean_float(atr_value),
        "contract_size": _clean_float(contract_size),
        "raw_lot": None,
        "rounded_lot": None,
        "volume_min": _clean_float(volume_min),
        "volume_max": _clean_float(volume_max),
        "volume_step": _clean_float(volume_step),
        "required_margin": _clean_float(required_margin),
        "free_margin": _clean_float(free_margin),
        "sizing_status": SIZING_INVALID,
        "increased_risk_due_to_min_lot": False,
    }

    if (
        equity is None
        or risk_percent is None
        or entry_price is None
        or atr_value is None
        or initial_stop_loss_atr is None
        or atr_value <= 0
        or entry_price <= 0
        or risk_percent <= 0
        or contract_size <= 0
    ):
        return diagnostics

    initial_stop_price = entry_price - initial_stop_loss_atr * atr_value
    risk_per_unit = entry_price - initial_stop_price
    diagnostics["initial_stop_price"] = _clean_float(initial_stop_price)
    diagnostics["risk_per_unit"] = _clean_float(risk_per_unit)

    if risk_per_unit <= 0:
        return diagnostics

    risk_amount = equity * risk_percent / 100.0
    raw_lot = risk_amount / (risk_per_unit * contract_size)
    diagnostics["risk_amount"] = _clean_float(risk_amount)
    diagnostics["raw_lot"] = _clean_float(raw_lot)

    rounded_lot = round_down_to_step(raw_lot, volume_step)
    if volume_max is not None and rounded_lot > volume_max:
        rounded_lot = round_down_to_step(volume_max, volume_step)

    increased_risk = False
    status = SIZING_OK
    if rounded_lot < volume_min:
        if allow_min_lot_rounding:
            rounded_lot = volume_min
            increased_risk = True
        else:
            status = SIZING_LOT_BELOW_MIN

    diagnostics["rounded_lot"] = _clean_float(rounded_lot)
    diagnostics["increased_risk_due_to_min_lot"] = increased_risk

    # Margin check only matters once we have a usable lot.
    if (
        status == SIZING_OK
        and required_margin is not None
        and free_margin is not None
        and required_margin > free_margin
    ):
        status = SIZING_MARGIN_INSUFFICIENT

    diagnostics["sizing_status"] = status
    return diagnostics


# ---------------------------------------------------------------------------
# Trailing-stop computation (upward only)
# ---------------------------------------------------------------------------
def compute_trailing_update(
    *,
    latest_close: Optional[float],
    atr_value: Optional[float],
    trailing_stop_atr: Optional[float],
    current_sl: Optional[float],
    current_bid: Optional[float],
    stops_level_points: Optional[float],
    point: Optional[float],
) -> dict:
    """Compute the new trailing SL candidate and whether it would improve.

    The candidate is ``latest_close - trailing_stop_atr * atr``. It only
    "improves" when it is **above** the current SL (never downward), **below**
    the current bid, and -- if the broker exposes a stop level -- at least the
    minimum stop distance away from the bid.
    """
    result = {
        "current_sl": _clean_float(current_sl),
        "trailing_stop_candidate": None,
        "would_improve_sl": False,
        "update_sent": False,
    }
    if latest_close is None or atr_value is None or trailing_stop_atr is None:
        return result

    candidate = latest_close - trailing_stop_atr * atr_value
    result["trailing_stop_candidate"] = _clean_float(candidate)

    if current_bid is None:
        return result

    # Never downward: candidate must beat the existing SL (0/None == no SL yet).
    sl_floor = current_sl if (current_sl is not None and current_sl > 0) else 0.0
    if candidate <= sl_floor:
        return result
    if candidate >= current_bid:
        return result

    if stops_level_points is not None and point is not None and stops_level_points > 0:
        min_distance = stops_level_points * point
        if (current_bid - candidate) < min_distance:
            return result

    result["would_improve_sl"] = True
    return result


# ---------------------------------------------------------------------------
# Order sending (demo execution only -- never reached in dry-run)
# ---------------------------------------------------------------------------
def _order_result_dict(mt5, result) -> dict:  # type: ignore[no-untyped-def]
    """Normalise an ``order_send`` result object into a JSON-safe dict."""
    if result is None:
        return {
            "retcode": None,
            "order": None,
            "deal": None,
            "price": None,
            "volume": None,
            "comment": "order_send returned None",
            "message": "order_send returned None",
        }
    retcode = getattr(result, "retcode", None)
    done = getattr(mt5, "TRADE_RETCODE_DONE", 10009)
    return {
        "retcode": retcode,
        "order": getattr(result, "order", None),
        "deal": getattr(result, "deal", None),
        "price": _clean_float(getattr(result, "price", None)),
        "volume": _clean_float(getattr(result, "volume", None)),
        "comment": getattr(result, "comment", None),
        "message": "done" if retcode == done else f"retcode={retcode}",
        "ok": retcode == done,
    }


def send_buy_order(  # type: ignore[no-untyped-def]
    mt5,
    *,
    symbol: str,
    volume: float,
    price: float,
    sl: float,
    tp: Optional[float],
    deviation: int,
    magic: int,
    spec: dict,
) -> dict:
    """Send a BUY market order. **Only** called in demo_execution after all gates.

    There is intentionally no SELL/SHORT counterpart anywhere in this module.
    """
    request = {
        "action": getattr(mt5, "TRADE_ACTION_DEAL"),
        "symbol": symbol,
        "volume": float(volume),
        "type": getattr(mt5, "ORDER_TYPE_BUY"),
        "price": float(price),
        "sl": float(sl),
        "tp": float(tp) if tp else 0.0,
        "deviation": int(deviation),
        "magic": int(magic),
        "comment": ORDER_COMMENT,
        "type_time": getattr(mt5, "ORDER_TIME_GTC"),
        "type_filling": _resolve_filling_mode(mt5, spec.get("filling_mode")),
    }
    result = mt5.order_send(request)
    return _order_result_dict(mt5, result)


def send_trailing_update(  # type: ignore[no-untyped-def]
    mt5,
    *,
    symbol: str,
    ticket: int,
    sl: float,
    tp: Optional[float],
    magic: int,
) -> dict:
    """Send an SLTP modification to raise the stop. Never closes the position."""
    request = {
        "action": getattr(mt5, "TRADE_ACTION_SLTP"),
        "position": int(ticket),
        "symbol": symbol,
        "sl": float(sl),
        "tp": float(tp) if tp else 0.0,
        "magic": int(magic),
        "comment": TRAILING_COMMENT,
    }
    result = mt5.order_send(request)
    return _order_result_dict(mt5, result)


# ---------------------------------------------------------------------------
# Decision record assembly
# ---------------------------------------------------------------------------
def _new_decision_id() -> str:
    return uuid.uuid4().hex[:16]


def empty_sizing() -> dict:
    """A sizing block with everything unknown (used by manual refusals)."""
    return {
        "equity": None,
        "risk_percent": None,
        "risk_amount": None,
        "entry_price": None,
        "initial_stop_price": None,
        "risk_per_unit": None,
        "atr_value": None,
        "contract_size": None,
        "raw_lot": None,
        "rounded_lot": None,
        "volume_min": None,
        "volume_max": None,
        "volume_step": None,
        "required_margin": None,
        "free_margin": None,
        "sizing_status": SIZING_INVALID,
        "increased_risk_due_to_min_lot": False,
    }


def empty_position() -> dict:
    """A position block with no open position (used by manual refusals)."""
    return {
        "has_position": False,
        "ticket": None,
        "volume": None,
        "price_open": None,
        "sl": None,
        "tp": None,
    }


def unknown_account() -> dict:
    """An account block when MT5 was never contacted (manual refusals)."""
    return {
        "login": None,
        "server": None,
        "trade_mode": "unknown",
        "is_demo": False,
        "equity": None,
        "free_margin": None,
    }


def manual_refusal_decision(
    config: dict,
    *,
    reasons: list[str],
    symbol: Optional[str] = None,
    execution_enabled: bool = False,
    demo_only: bool = True,
    generated_at: Optional[datetime] = None,
) -> dict:
    """Build a fully-formed *refused* decision without contacting MT5.

    Used when a request is refused before any terminal connection is attempted
    (e.g. an unsupported config, or a demo run without confirmation). The shape
    matches a real decision so the UI can render it in the same card.
    """
    generated_at = generated_at or datetime.now(timezone.utc)
    return {
        "decision_id": _new_decision_id(),
        "generated_at": generated_at.isoformat(),
        "execution_robot_version": EXECUTION_ROBOT_VERSION,
        "mode": MODE_REFUSED,
        "execution_enabled": bool(execution_enabled),
        "demo_only": bool(demo_only),
        "account": unknown_account(),
        "symbol": symbol or config.get("symbol") or DEFAULT_SYMBOL,
        "timeframe": SUPPORTED_TIMEFRAME,
        "strategy_id": config.get("strategy_id"),
        "signal": {
            "signal_time": None,
            "signal_type": None,
            "reason_human": None,
            "strategy_regime": None,
        },
        "market": {"bid": None, "ask": None, "latest_close": None, "atr": None},
        "sizing": empty_sizing(),
        "position_state": empty_position(),
        "intended_action": ACTION_REFUSED,
        "refusal_reasons": list(reasons),
        "notes": [],
        "order_result": None,
        "trailing": {
            "current_sl": None,
            "trailing_stop_candidate": None,
            "would_improve_sl": False,
            "update_sent": False,
        },
    }


def build_decision_skeleton(
    *,
    config: dict,
    symbol: str,
    mode: str,
    execution_enabled: bool,
    demo_only: bool,
    account: dict,
    signal_record: dict,
    market: dict,
    sizing: dict,
    position_state: dict,
    generated_at: datetime,
) -> dict:
    """Assemble the full ``latest_execution_decision`` record (pre-action)."""
    state = signal_record.get("strategy_state") or {}
    return {
        "decision_id": _new_decision_id(),
        "generated_at": generated_at.isoformat(),
        "execution_robot_version": EXECUTION_ROBOT_VERSION,
        "mode": mode,
        "execution_enabled": bool(execution_enabled),
        "demo_only": bool(demo_only),
        "account": account,
        "symbol": symbol,
        "timeframe": SUPPORTED_TIMEFRAME,
        "strategy_id": config.get("strategy_id"),
        "signal": {
            "signal_time": signal_record.get("signal_time"),
            "signal_type": signal_record.get("signal_type"),
            "reason_human": signal_record.get("reason_human"),
            "strategy_regime": state.get("strategy_regime"),
        },
        "market": {
            "bid": market.get("bid"),
            "ask": market.get("ask"),
            "latest_close": signal_record.get("close_price"),
            "atr": signal_record.get("atr_value"),
        },
        "sizing": sizing,
        "position_state": position_state,
        "intended_action": ACTION_NO_ACTION,
        "refusal_reasons": [],
        "notes": [],
        "order_result": None,
        "trailing": {
            "current_sl": position_state.get("sl"),
            "trailing_stop_candidate": None,
            "would_improve_sl": False,
            "update_sent": False,
        },
    }


# ---------------------------------------------------------------------------
# Orchestration (one decision). MT5 access is injected for testability.
# ---------------------------------------------------------------------------
def run_once(  # type: ignore[no-untyped-def]
    config: dict,
    store: ExecutionStore,
    mt5,
    *,
    symbol: Optional[str] = None,
    bars: int = bridge.DEFAULT_BARS,
    execution_enabled: bool = False,
    demo_only: bool = True,
    confirm_demo_execution: bool = False,
    magic: int = DEFAULT_MAGIC,
    deviation: int = DEFAULT_DEVIATION,
    allow_min_lot_rounding: bool = False,
    generated_at: Optional[datetime] = None,
) -> dict:
    """Run one execution decision and persist it through the store.

    This is the single entry point shared by the CLI, the manager (one-shot) and
    the polling subprocess. It validates the config, reads the closed-candle
    signal (reusing the bridge), inspects the account/market/position read-only,
    computes sizing/trailing, applies the demo safety gates and -- only in
    ``demo_execution`` with every gate satisfied -- sends an order.

    Returns the decision record (also written to disk).
    """
    validate_execution_config(config)
    generated_at = generated_at or datetime.now(timezone.utc)
    requested_symbol = symbol or config.get("symbol") or DEFAULT_SYMBOL

    resolved_symbol = bridge.resolve_symbol(mt5, requested_symbol)

    # --- read-only signal (reuses the bridge -> identical to the backtester) ---
    fetch_ohlc_fn = bridge.make_fetch_ohlc_fn(mt5)
    df = fetch_ohlc_fn(resolved_symbol, SUPPORTED_TIMEFRAME, bars)
    closed = bridge.select_closed_candles(df)
    market_context = bridge.read_market_context(mt5, resolved_symbol)
    signal_record = bridge.build_signal_record(
        config,
        closed,
        symbol=resolved_symbol,
        generated_at=generated_at,
        market_context=market_context,
    )

    # --- read-only account / symbol / market / position ---
    account = read_account(mt5)
    spec = read_symbol_spec(mt5, resolved_symbol, config)
    market = read_market(mt5, resolved_symbol)
    position_state = read_position(mt5, resolved_symbol, magic)

    exit_params = dict(config.get("exit_parameters", {}))
    risk_params = dict(config.get("risk_parameters", {}))
    initial_stop_loss_atr = _clean_float(exit_params.get("initial_stop_loss_atr"))
    trailing_stop_atr = _clean_float(exit_params.get("trailing_stop_atr"))
    take_profit_atr = _clean_float(exit_params.get("take_profit_atr"))
    risk_percent = _clean_float(risk_params.get("risk_percent"))

    entry_price = market.get("ask")
    atr_value = _clean_float(signal_record.get("atr_value"))
    latest_close = _clean_float(signal_record.get("close_price"))

    # Margin estimate (read-only) for the sizing margin check.
    required_margin = _estimate_required_margin(
        mt5,
        symbol=resolved_symbol,
        volume=None,  # filled after we know the lot; see below
        price=entry_price,
    )

    # Sizing is computed for the open path even when no BUY fires, so the UI can
    # always show the diagnostics. We re-run the margin check with the real lot.
    sizing = compute_sizing(
        equity=account.get("equity"),
        risk_percent=risk_percent,
        entry_price=entry_price,
        atr_value=atr_value,
        initial_stop_loss_atr=initial_stop_loss_atr,
        spec=spec,
        free_margin=account.get("free_margin"),
        required_margin=None,  # provisional; recomputed below with the lot
        allow_min_lot_rounding=allow_min_lot_rounding,
    )
    rounded_lot = sizing.get("rounded_lot")
    if rounded_lot:
        required_margin = _estimate_required_margin(
            mt5, symbol=resolved_symbol, volume=rounded_lot, price=entry_price
        )
        sizing = compute_sizing(
            equity=account.get("equity"),
            risk_percent=risk_percent,
            entry_price=entry_price,
            atr_value=atr_value,
            initial_stop_loss_atr=initial_stop_loss_atr,
            spec=spec,
            free_margin=account.get("free_margin"),
            required_margin=required_margin,
            allow_min_lot_rounding=allow_min_lot_rounding,
        )

    # --- determine effective mode + gates ---
    execution_requested = bool(execution_enabled)
    gate_reasons = _execution_gate_reasons(
        execution_requested=execution_requested,
        demo_only=demo_only,
        confirm_demo_execution=confirm_demo_execution,
        account=account,
    )
    if execution_requested and gate_reasons:
        mode = MODE_REFUSED
    elif execution_requested:
        mode = MODE_DEMO_EXECUTION
    else:
        mode = MODE_DRY_RUN

    decision = build_decision_skeleton(
        config=config,
        symbol=resolved_symbol,
        mode=mode,
        execution_enabled=execution_enabled,
        demo_only=demo_only,
        account=account,
        signal_record=signal_record,
        market=market,
        sizing=sizing,
        position_state=position_state,
        generated_at=generated_at,
    )

    # A wholesale execution refusal (gate failed) short-circuits everything.
    if mode == MODE_REFUSED:
        decision["intended_action"] = ACTION_REFUSED
        decision["refusal_reasons"] = gate_reasons
        store.write_decision(decision)
        return decision

    signal_type = signal_record.get("signal_type")
    signal_time = pd.Timestamp(signal_record["signal_time"])
    regime = (signal_record.get("strategy_state") or {}).get("strategy_regime")
    key = store.make_key(config["strategy_id"], resolved_symbol, SUPPORTED_TIMEFRAME)

    if position_state["has_position"]:
        _decide_trailing(
            decision,
            mt5=mt5,
            mode=mode,
            symbol=resolved_symbol,
            position_state=position_state,
            latest_close=latest_close,
            atr_value=atr_value,
            trailing_stop_atr=trailing_stop_atr,
            take_profit_atr=take_profit_atr,
            spec=spec,
            market=market,
            regime=regime,
            magic=magic,
        )
    else:
        _decide_entry(
            decision,
            mt5=mt5,
            mode=mode,
            store=store,
            key=key,
            symbol=resolved_symbol,
            signal_type=signal_type,
            signal_time=signal_time,
            sizing=sizing,
            take_profit_atr=take_profit_atr,
            atr_value=atr_value,
            entry_price=entry_price,
            deviation=deviation,
            magic=magic,
            spec=spec,
            generated_at=generated_at,
        )

    store.write_decision(decision)
    return decision


def _execution_gate_reasons(
    *,
    execution_requested: bool,
    demo_only: bool,
    confirm_demo_execution: bool,
    account: dict,
) -> list[str]:
    """Collect hard execution-gate failures (only relevant when executing)."""
    reasons: list[str] = []
    if not execution_requested:
        return reasons
    if not demo_only:
        reasons.append(REFUSE_DEMO_ONLY_REQUIRED)
    if not confirm_demo_execution:
        reasons.append(REFUSE_NOT_CONFIRMED)
    if not account.get("is_demo"):
        reasons.append(REFUSE_ACCOUNT_NOT_DEMO)
    return reasons


def _decide_entry(  # type: ignore[no-untyped-def]
    decision: dict,
    *,
    mt5,
    mode: str,
    store: ExecutionStore,
    key: str,
    symbol: str,
    signal_type: Optional[str],
    signal_time: pd.Timestamp,
    sizing: dict,
    take_profit_atr: Optional[float],
    atr_value: Optional[float],
    entry_price: Optional[float],
    deviation: int,
    magic: int,
    spec: dict,
    generated_at: datetime,
) -> None:
    """No existing position: decide on a fresh BUY entry (long-only)."""
    if signal_type != "BUY":
        decision["intended_action"] = ACTION_NO_ACTION
        return

    # Duplicate guard: never send a second order for the same closed candle.
    if store.already_opened_for_signal(key, signal_time):
        decision["intended_action"] = ACTION_NO_ACTION
        decision["notes"].append(NOTE_DUPLICATE)
        return

    # Sizing/margin must be valid before an open is possible.
    status = sizing.get("sizing_status")
    if status != SIZING_OK:
        decision["intended_action"] = ACTION_REFUSED
        if status == SIZING_LOT_BELOW_MIN:
            decision["refusal_reasons"].append(REFUSE_LOT_BELOW_MIN)
        elif status == SIZING_MARGIN_INSUFFICIENT:
            decision["refusal_reasons"].append(REFUSE_MARGIN_INSUFFICIENT)
        else:
            decision["refusal_reasons"].append(REFUSE_INVALID_SIZING)
        return

    if mode != MODE_DEMO_EXECUTION:
        # Dry-run: report what would happen; never send.
        decision["intended_action"] = ACTION_WOULD_OPEN_BUY
        return

    # Demo execution: send the BUY, then record the open-attempt for dedup.
    tp_price = None
    if take_profit_atr is not None and entry_price is not None and atr_value is not None:
        tp_price = entry_price + take_profit_atr * atr_value

    order_result = send_buy_order(
        mt5,
        symbol=symbol,
        volume=sizing["rounded_lot"],
        price=entry_price,
        sl=sizing["initial_stop_price"],
        tp=tp_price,
        deviation=deviation,
        magic=magic,
        spec=spec,
    )
    decision["order_result"] = order_result
    # Mark processed regardless of retcode: one attempt per candle (no retry storm).
    store.mark_open_processed(
        key, signal_time, decision["decision_id"], generated_at.isoformat()
    )
    if order_result.get("ok"):
        decision["intended_action"] = ACTION_OPENED_BUY
    else:
        decision["intended_action"] = ACTION_REFUSED
        decision["refusal_reasons"].append(REFUSE_SEND_FAILED)


def _decide_trailing(  # type: ignore[no-untyped-def]
    decision: dict,
    *,
    mt5,
    mode: str,
    symbol: str,
    position_state: dict,
    latest_close: Optional[float],
    atr_value: Optional[float],
    trailing_stop_atr: Optional[float],
    take_profit_atr: Optional[float],
    spec: dict,
    market: dict,
    regime: Optional[str],
    magic: int,
) -> None:
    """Existing BUY position: trail the stop upward only. Never open / close."""
    decision["notes"].append(NOTE_ONE_POSITION)
    # A bearish flip ends the setup, but v1.8 never closes -- just record it.
    if regime == "bearish":
        decision["notes"].append(NOTE_SETUP_ENDED)

    trailing = compute_trailing_update(
        latest_close=latest_close,
        atr_value=atr_value,
        trailing_stop_atr=trailing_stop_atr,
        current_sl=position_state.get("sl"),
        current_bid=market.get("bid"),
        stops_level_points=spec.get("stops_level_points"),
        point=spec.get("point"),
    )
    decision["trailing"] = trailing

    if not trailing["would_improve_sl"]:
        decision["intended_action"] = ACTION_NO_ACTION
        return

    if mode != MODE_DEMO_EXECUTION:
        decision["intended_action"] = ACTION_WOULD_UPDATE_TRAILING
        return

    tp = position_state.get("tp")
    order_result = send_trailing_update(
        mt5,
        symbol=symbol,
        ticket=position_state["ticket"],
        sl=trailing["trailing_stop_candidate"],
        tp=tp,
        magic=magic,
    )
    decision["order_result"] = order_result
    if order_result.get("ok"):
        decision["intended_action"] = ACTION_UPDATED_TRAILING
        decision["trailing"]["update_sent"] = True
    else:
        decision["intended_action"] = ACTION_REFUSED
        decision["refusal_reasons"].append(REFUSE_SEND_FAILED)


def _estimate_required_margin(  # type: ignore[no-untyped-def]
    mt5,
    *,
    symbol: str,
    volume: Optional[float],
    price: Optional[float],
) -> Optional[float]:
    """Best-effort BUY margin via ``order_calc_margin`` (read-only). None on failure."""
    if volume is None or price is None or volume <= 0:
        return None
    calc = getattr(mt5, "order_calc_margin", None)
    if not callable(calc):
        return None
    try:
        margin = calc(getattr(mt5, "ORDER_TYPE_BUY"), symbol, float(volume), float(price))
    except Exception:  # pragma: no cover - broker-dependent
        return None
    return _clean_float(margin)
