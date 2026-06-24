"""Tests for the Strategy Lab v1.8 MT5 **demo execution robot** core.

No real MetaTrader 5 terminal is required: MT5 is replaced by a small fake that
records any ``order_send`` calls, so the safety gates can be exercised exactly.
The exported D config is produced by the real
:func:`app.strategy_lab.lab_service.export_config` (no market data needed).

The over-arching contract these tests pin down:

    * dry-run never sends an order;
    * orders are sent only on a *detected demo* account, with execution enabled
      and confirmed, for a supported D config, and only as a fresh BUY;
    * an existing position / a duplicate signal_time never opens a second order;
    * the stop is only ever trailed *upward*, never closed, never SELL.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.strategy_lab import lab_service
from app.strategy_lab import mt5_execution_robot as robot
from app.strategy_lab.execution_store import ExecutionStore

D_PRESET = "D_supertrend_h4_trailing_risk"
C_PRESET = "C_donchian_h1_fixed_atr_risk"


# ---------------------------------------------------------------------------
# Fixtures / synthetic data
# ---------------------------------------------------------------------------
@pytest.fixture()
def d_config() -> dict:
    return lab_service.export_config(preset_id=D_PRESET)


@pytest.fixture()
def store(tmp_path: Path) -> ExecutionStore:
    return ExecutionStore(tmp_path / "exec")


def _supertrend_series(n: int = 400) -> pd.DataFrame:
    times = pd.date_range("2020-01-01", periods=n, freq="4h")
    dip_n = max(2, min(60, n // 2))
    dip = np.linspace(2000.0, 1900.0, num=dip_n)
    rally = np.linspace(1900.0, 2400.0, num=n - dip_n)
    close = np.concatenate([dip, rally])
    wiggle = np.sin(np.arange(n) / 3.0) * 4.0
    return pd.DataFrame(
        {
            "datetime": times,
            "open": close - wiggle * 0.5,
            "high": close + np.abs(wiggle) + 5.0,
            "low": close - np.abs(wiggle) - 5.0,
            "close": close,
        }
    )


def _first_buy_index(df: pd.DataFrame) -> int:
    from app.strategy_lab import strategies

    sig = strategies.supertrend_strategy(df, atr_period=10, multiplier=2.0)
    buys = np.where(sig["signal"].to_numpy() == 1)[0]
    assert len(buys), "synthetic series did not produce a bullish flip"
    return int(buys[0])


def _to_mt5_rates(df: pd.DataFrame) -> np.ndarray:
    dtype = [
        ("time", "<i8"),
        ("open", "<f8"),
        ("high", "<f8"),
        ("low", "<f8"),
        ("close", "<f8"),
        ("tick_volume", "<i8"),
        ("spread", "<i4"),
        ("real_volume", "<i8"),
    ]
    secs = df["datetime"].astype("int64") // 1_000_000_000
    rows = [
        (int(t), float(o), float(h), float(low), float(c), 100, 20, 0)
        for t, o, h, low, c in zip(secs, df["open"], df["high"], df["low"], df["close"])
    ]
    return np.array(rows, dtype=dtype)


def _buy_rates() -> np.ndarray:
    df = _supertrend_series()
    k = _first_buy_index(df)
    return _to_mt5_rates(df.iloc[: k + 2].reset_index(drop=True))


def _none_rates() -> np.ndarray:
    """Latest closed candle is the bar just before the flip -> no entry."""
    df = _supertrend_series()
    k = _first_buy_index(df)
    return _to_mt5_rates(df.iloc[: k + 1].reset_index(drop=True))


# ---------------------------------------------------------------------------
# Fake MetaTrader5 module: read-only inspection + a recorded order_send
# ---------------------------------------------------------------------------
class FakeMT5:
    """Stand-in for MetaTrader5 that records orders instead of placing them."""

    TIMEFRAME_H1 = 16385
    TIMEFRAME_H4 = 16388

    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_SLTP = 6
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_FOK = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_RETURN = 2
    SYMBOL_FILLING_FOK = 1
    SYMBOL_FILLING_IOC = 2
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1
    ACCOUNT_TRADE_MODE_DEMO = 0
    ACCOUNT_TRADE_MODE_CONTEST = 1
    ACCOUNT_TRADE_MODE_REAL = 2
    TRADE_RETCODE_DONE = 10009

    def __init__(
        self,
        rates: np.ndarray,
        *,
        symbols: tuple[str, ...] = ("XAUUSDrfd",),
        trade_mode: int = 0,  # demo
        equity: float = 10_000.0,
        margin_free: float = 100_000.0,
        positions: tuple = (),
        margin: float = 50.0,
        retcode: int = 10009,
        bid: float | None = None,
        ask: float | None = None,
    ) -> None:
        self._rates = rates
        self._symbols = symbols
        self._trade_mode = trade_mode
        self._equity = equity
        self._margin_free = margin_free
        self._positions = positions
        self._margin = margin
        self._retcode = retcode
        last_close = float(rates["close"][-1])
        self._bid = bid if bid is not None else last_close - 0.2
        self._ask = ask if ask is not None else last_close + 0.2
        self.sent: list[dict] = []

    # connection
    def initialize(self) -> bool:
        return True

    def shutdown(self) -> None:
        pass

    def last_error(self):
        return (0, "ok")

    def terminal_info(self):
        return SimpleNamespace(connected=True)

    # account / symbol / market (read-only)
    def account_info(self):
        return SimpleNamespace(
            login=5_000_111,
            server="MetaQuotes-Demo",
            trade_mode=self._trade_mode,
            equity=self._equity,
            margin_free=self._margin_free,
        )

    def symbol_info(self, name: str):
        if name not in self._symbols:
            return None
        return SimpleNamespace(
            name=name,
            visible=True,
            volume_min=0.01,
            volume_max=50.0,
            volume_step=0.01,
            trade_contract_size=100.0,
            filling_mode=self.SYMBOL_FILLING_IOC,
            trade_stops_level=0,
            point=0.01,
            digits=2,
        )

    def symbol_select(self, name: str, enable: bool) -> bool:  # noqa: FBT001
        return True

    def symbol_info_tick(self, name: str):
        return SimpleNamespace(bid=self._bid, ask=self._ask)

    def copy_rates_from_pos(self, symbol: str, timeframe, start: int, count: int):
        return self._rates[-count:] if count else self._rates

    def positions_get(self, symbol=None):
        return self._positions

    def order_calc_margin(self, order_type, symbol, volume, price):
        return self._margin

    # the ONE place an order is sent (recorded, never reaches a broker)
    def order_send(self, request: dict):
        self.sent.append(dict(request))
        return SimpleNamespace(
            retcode=self._retcode,
            order=987654,
            deal=123456,
            price=request.get("price", self._ask),
            volume=request.get("volume", 0.0),
            comment="done" if self._retcode == self.TRADE_RETCODE_DONE else "rejected",
        )


def _buy_position(
    fake: FakeMT5, *, sl: float, tp: float = 0.0, magic: int = robot.DEFAULT_MAGIC
):
    """Attach a single open BUY position to a fake."""
    pos = SimpleNamespace(
        ticket=222333,
        magic=magic,
        type=fake.POSITION_TYPE_BUY,
        volume=0.10,
        price_open=float(fake._rates["close"][-1]) - 50.0,
        sl=sl,
        tp=tp,
    )
    fake._positions = (pos,)
    return pos


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------
def test_valid_d_config_passes(d_config: dict) -> None:
    robot.validate_execution_config(d_config)  # must not raise


def test_unsupported_c_config_refused() -> None:
    c_config = lab_service.export_config(preset_id=C_PRESET)
    with pytest.raises(robot.ExecutionError, match="only D SuperTrend H4"):
        robot.validate_execution_config(c_config)


def test_ml_filter_enabled_refused(d_config: dict) -> None:
    d_config["ml_filter_enabled"] = True
    with pytest.raises(robot.ExecutionError, match="ml_filter_enabled"):
        robot.validate_execution_config(d_config)


def test_non_long_only_refused(d_config: dict) -> None:
    d_config["direction_mode"] = "both"
    with pytest.raises(robot.ExecutionError):
        robot.validate_execution_config(d_config)


# ---------------------------------------------------------------------------
# Dry-run: never sends an order
# ---------------------------------------------------------------------------
def test_dry_run_would_open_buy_no_order(d_config: dict, store: ExecutionStore) -> None:
    fake = FakeMT5(_buy_rates())
    decision = robot.run_once(d_config, store, fake, bars=500)
    assert decision["mode"] == robot.MODE_DRY_RUN
    assert decision["intended_action"] == robot.ACTION_WOULD_OPEN_BUY
    assert fake.sent == []  # dry-run never sends
    assert store.read_latest()["decision_id"] == decision["decision_id"]


def test_dry_run_no_action_when_no_signal(d_config: dict, store: ExecutionStore) -> None:
    fake = FakeMT5(_none_rates())
    decision = robot.run_once(d_config, store, fake, bars=500)
    assert decision["intended_action"] == robot.ACTION_NO_ACTION
    assert fake.sent == []


def test_dry_run_safe_on_live_account(d_config: dict, store: ExecutionStore) -> None:
    """Dry-run never executes, so it is safe even on a live account."""
    fake = FakeMT5(_buy_rates(), trade_mode=FakeMT5.ACCOUNT_TRADE_MODE_REAL)
    decision = robot.run_once(d_config, store, fake, bars=500)
    assert decision["mode"] == robot.MODE_DRY_RUN
    assert fake.sent == []
    assert decision["account"]["is_demo"] is False
    assert decision["account"]["trade_mode"] == "real"


# ---------------------------------------------------------------------------
# Demo execution: account gating
# ---------------------------------------------------------------------------
def test_demo_execution_opens_buy_on_demo(d_config: dict, store: ExecutionStore) -> None:
    fake = FakeMT5(_buy_rates(), trade_mode=FakeMT5.ACCOUNT_TRADE_MODE_DEMO)
    decision = robot.run_once(
        d_config,
        store,
        fake,
        bars=500,
        execution_enabled=True,
        confirm_demo_execution=True,
    )
    assert decision["mode"] == robot.MODE_DEMO_EXECUTION
    assert decision["intended_action"] == robot.ACTION_OPENED_BUY
    assert len(fake.sent) == 1
    request = fake.sent[0]
    # BUY order request shape
    assert request["action"] == FakeMT5.TRADE_ACTION_DEAL
    assert request["type"] == FakeMT5.ORDER_TYPE_BUY
    assert request["symbol"] == "XAUUSDrfd"
    assert request["volume"] == decision["sizing"]["rounded_lot"]
    assert request["sl"] == decision["sizing"]["initial_stop_price"]
    assert request["sl"] < request["price"]
    assert request["magic"] == robot.DEFAULT_MAGIC
    assert request["comment"] == robot.ORDER_COMMENT
    assert request["deviation"] == robot.DEFAULT_DEVIATION
    assert request["type_time"] == FakeMT5.ORDER_TIME_GTC


def test_live_account_refused(d_config: dict, store: ExecutionStore) -> None:
    fake = FakeMT5(_buy_rates(), trade_mode=FakeMT5.ACCOUNT_TRADE_MODE_REAL)
    decision = robot.run_once(
        d_config,
        store,
        fake,
        bars=500,
        execution_enabled=True,
        confirm_demo_execution=True,
    )
    assert decision["mode"] == robot.MODE_REFUSED
    assert decision["intended_action"] == robot.ACTION_REFUSED
    assert robot.REFUSE_ACCOUNT_NOT_DEMO in decision["refusal_reasons"]
    assert fake.sent == []


def test_unknown_account_refused(d_config: dict, store: ExecutionStore) -> None:
    fake = FakeMT5(_buy_rates(), trade_mode=999)  # not a known demo/real constant
    decision = robot.run_once(
        d_config,
        store,
        fake,
        bars=500,
        execution_enabled=True,
        confirm_demo_execution=True,
    )
    assert decision["mode"] == robot.MODE_REFUSED
    assert decision["account"]["trade_mode"] == "unknown"
    assert fake.sent == []


def test_demo_execution_requires_confirmation(d_config: dict, store: ExecutionStore) -> None:
    fake = FakeMT5(_buy_rates(), trade_mode=FakeMT5.ACCOUNT_TRADE_MODE_DEMO)
    decision = robot.run_once(
        d_config,
        store,
        fake,
        bars=500,
        execution_enabled=True,
        confirm_demo_execution=False,
    )
    assert decision["mode"] == robot.MODE_REFUSED
    assert robot.REFUSE_NOT_CONFIRMED in decision["refusal_reasons"]
    assert fake.sent == []


# ---------------------------------------------------------------------------
# Duplicate prevention + one-position-only
# ---------------------------------------------------------------------------
def test_duplicate_signal_time_prevents_second_order(
    d_config: dict, store: ExecutionStore
) -> None:
    fake = FakeMT5(_buy_rates(), trade_mode=FakeMT5.ACCOUNT_TRADE_MODE_DEMO)
    first = robot.run_once(
        d_config, store, fake, bars=500, execution_enabled=True, confirm_demo_execution=True
    )
    second = robot.run_once(
        d_config, store, fake, bars=500, execution_enabled=True, confirm_demo_execution=True
    )
    assert first["intended_action"] == robot.ACTION_OPENED_BUY
    assert second["intended_action"] == robot.ACTION_NO_ACTION
    assert robot.NOTE_DUPLICATE in second["notes"]
    assert len(fake.sent) == 1  # only ONE order, ever


def test_existing_position_prevents_duplicate_entry(
    d_config: dict, store: ExecutionStore
) -> None:
    fake = FakeMT5(_buy_rates(), trade_mode=FakeMT5.ACCOUNT_TRADE_MODE_DEMO)
    # An open BUY already exists with a very high SL -> no trailing improvement.
    _buy_position(fake, sl=9_999.0)
    decision = robot.run_once(
        d_config, store, fake, bars=500, execution_enabled=True, confirm_demo_execution=True
    )
    assert decision["position_state"]["has_position"] is True
    assert decision["intended_action"] == robot.ACTION_NO_ACTION
    assert robot.NOTE_ONE_POSITION in decision["notes"]
    assert fake.sent == []  # never opens a second position


# ---------------------------------------------------------------------------
# Sizing + rounding + margin
# ---------------------------------------------------------------------------
def test_sizing_rounds_down_to_step() -> None:
    spec = {
        "contract_size": 100.0,
        "volume_min": 0.01,
        "volume_max": 50.0,
        "volume_step": 0.01,
    }
    sizing = robot.compute_sizing(
        equity=10_000.0,
        risk_percent=1.0,
        entry_price=2000.0,
        atr_value=10.0,
        initial_stop_loss_atr=2.5,
        spec=spec,
        free_margin=100_000.0,
        required_margin=50.0,
        allow_min_lot_rounding=False,
    )
    # risk_amount = 100; risk_per_unit = 2.5*10 = 25; raw = 100/(25*100)=0.04
    assert sizing["risk_amount"] == pytest.approx(100.0)
    assert sizing["risk_per_unit"] == pytest.approx(25.0)
    assert sizing["raw_lot"] == pytest.approx(0.04)
    assert sizing["rounded_lot"] == pytest.approx(0.04)
    assert sizing["sizing_status"] == robot.SIZING_OK
    assert sizing["increased_risk_due_to_min_lot"] is False


def test_sizing_lot_below_min_refused_then_allowed() -> None:
    spec = {
        "contract_size": 100.0,
        "volume_min": 0.10,  # min above the computed lot
        "volume_max": 50.0,
        "volume_step": 0.01,
    }
    refused = robot.compute_sizing(
        equity=1_000.0,
        risk_percent=0.5,
        entry_price=2000.0,
        atr_value=10.0,
        initial_stop_loss_atr=2.5,
        spec=spec,
        free_margin=100_000.0,
        required_margin=50.0,
        allow_min_lot_rounding=False,
    )
    assert refused["sizing_status"] == robot.SIZING_LOT_BELOW_MIN

    allowed = robot.compute_sizing(
        equity=1_000.0,
        risk_percent=0.5,
        entry_price=2000.0,
        atr_value=10.0,
        initial_stop_loss_atr=2.5,
        spec=spec,
        free_margin=100_000.0,
        required_margin=50.0,
        allow_min_lot_rounding=True,
    )
    assert allowed["sizing_status"] == robot.SIZING_OK
    assert allowed["rounded_lot"] == pytest.approx(0.10)
    assert allowed["increased_risk_due_to_min_lot"] is True


def test_margin_insufficient_refusal(d_config: dict, store: ExecutionStore) -> None:
    fake = FakeMT5(
        _buy_rates(),
        trade_mode=FakeMT5.ACCOUNT_TRADE_MODE_DEMO,
        margin_free=1.0,  # almost nothing free
        margin=10_000.0,  # required margin far exceeds free
    )
    decision = robot.run_once(
        d_config, store, fake, bars=500, execution_enabled=True, confirm_demo_execution=True
    )
    assert decision["intended_action"] == robot.ACTION_REFUSED
    assert robot.REFUSE_MARGIN_INSUFFICIENT in decision["refusal_reasons"]
    assert fake.sent == []


# ---------------------------------------------------------------------------
# Trailing SL: upward only, never closes, SLTP shape
# ---------------------------------------------------------------------------
def test_trailing_upward_only_helper() -> None:
    # Candidate below current SL -> no improvement (never downward).
    down = robot.compute_trailing_update(
        latest_close=2000.0,
        atr_value=10.0,
        trailing_stop_atr=6.0,
        current_sl=1990.0,  # above the candidate (2000-60=1940)
        current_bid=2001.0,
        stops_level_points=0,
        point=0.01,
    )
    assert down["would_improve_sl"] is False

    # Candidate above current SL and below bid -> improves.
    up = robot.compute_trailing_update(
        latest_close=2100.0,
        atr_value=5.0,
        trailing_stop_atr=6.0,  # candidate = 2100-30 = 2070
        current_sl=2000.0,
        current_bid=2099.0,
        stops_level_points=0,
        point=0.01,
    )
    assert up["would_improve_sl"] is True
    assert up["trailing_stop_candidate"] == pytest.approx(2070.0)


def test_demo_trailing_update_sends_sltp(d_config: dict, store: ExecutionStore) -> None:
    # Use a NONE-signal rate set so the entry path is irrelevant; the position
    # drives the trailing path.
    fake = FakeMT5(_buy_rates(), trade_mode=FakeMT5.ACCOUNT_TRADE_MODE_DEMO)
    last_close = float(fake._rates["close"][-1])
    pos = _buy_position(fake, sl=last_close - 500.0)  # low SL -> room to trail up
    decision = robot.run_once(
        d_config, store, fake, bars=500, execution_enabled=True, confirm_demo_execution=True
    )
    assert decision["intended_action"] == robot.ACTION_UPDATED_TRAILING
    assert decision["trailing"]["would_improve_sl"] is True
    assert decision["trailing"]["update_sent"] is True
    assert len(fake.sent) == 1
    request = fake.sent[0]
    assert request["action"] == FakeMT5.TRADE_ACTION_SLTP
    assert request["position"] == pos.ticket
    assert request["comment"] == robot.TRAILING_COMMENT
    # New SL is above the old one (upward only) and below the bid.
    assert request["sl"] > pos.sl
    assert request["sl"] < fake._bid


def test_dry_run_trailing_does_not_send(d_config: dict, store: ExecutionStore) -> None:
    fake = FakeMT5(_buy_rates(), trade_mode=FakeMT5.ACCOUNT_TRADE_MODE_DEMO)
    last_close = float(fake._rates["close"][-1])
    _buy_position(fake, sl=last_close - 500.0)
    decision = robot.run_once(d_config, store, fake, bars=500)  # dry-run
    assert decision["intended_action"] == robot.ACTION_WOULD_UPDATE_TRAILING
    assert fake.sent == []


def test_no_close_logic_on_bearish_flip(d_config: dict, store: ExecutionStore) -> None:
    """A bearish setup with an open position is noted but never closed."""
    fake = FakeMT5(_none_rates(), trade_mode=FakeMT5.ACCOUNT_TRADE_MODE_DEMO)
    _buy_position(fake, sl=9_999.0)  # high SL -> no trailing either
    decision = robot.run_once(
        d_config, store, fake, bars=500, execution_enabled=True, confirm_demo_execution=True
    )
    # No order at all: v1.8 never closes a position.
    assert fake.sent == []
    assert decision["intended_action"] == robot.ACTION_NO_ACTION
    if decision["signal"]["strategy_regime"] == "bearish":
        assert robot.NOTE_SETUP_ENDED in decision["notes"]


# ---------------------------------------------------------------------------
# Order-send failure surfaces as a refusal
# ---------------------------------------------------------------------------
def test_order_send_failure_is_refused(d_config: dict, store: ExecutionStore) -> None:
    fake = FakeMT5(
        _buy_rates(), trade_mode=FakeMT5.ACCOUNT_TRADE_MODE_DEMO, retcode=10013
    )
    decision = robot.run_once(
        d_config, store, fake, bars=500, execution_enabled=True, confirm_demo_execution=True
    )
    assert decision["intended_action"] == robot.ACTION_REFUSED
    assert robot.REFUSE_SEND_FAILED in decision["refusal_reasons"]
    assert decision["order_result"]["retcode"] == 10013


# ---------------------------------------------------------------------------
# Store: decision persisted, history + dedup state
# ---------------------------------------------------------------------------
def test_decision_persisted_to_store(d_config: dict, store: ExecutionStore) -> None:
    fake = FakeMT5(_buy_rates())
    robot.run_once(d_config, store, fake, bars=500)
    assert store.latest_path.exists()
    assert store.events_path.exists()
    history = store.read_history()
    assert len(history) == 1
    assert history[0]["mode"] == robot.MODE_DRY_RUN
    assert history[0]["intended_action"] == robot.ACTION_WOULD_OPEN_BUY


# ---------------------------------------------------------------------------
# Static safety: no SELL/SHORT entry, no credentials, signal-only stays order-free
# ---------------------------------------------------------------------------
def test_robot_has_no_sell_or_short_entry_code() -> None:
    """The robot may send a BUY, but no SELL/SHORT *entry* code may exist."""
    source = Path(robot.__file__).read_text(encoding="utf-8")
    forbidden = (
        "ORDER_TYPE_SELL",
        "POSITION_TYPE_SELL",
        "TRADE_ACTION_CLOSE",
        "position_close",
        "Close(",
    )
    for token in forbidden:
        assert token not in source, f"{token} must not appear in the execution robot"


def test_robot_handles_no_credentials() -> None:
    """No broker login/password *handling* anywhere in the robot.

    The docstring may *promise* it never handles a login/password, so we check
    for actual credential-handling code tokens (a login call, a password literal
    or a credential keyword argument), not the prose.
    """
    source = Path(robot.__file__).read_text(encoding="utf-8")
    forbidden = (
        "mt5.login(",
        ".login(",
        "password=",
        '"password"',
        "'password'",
        "passwd",
        "login=",
    )
    for token in forbidden:
        assert token not in source, f"{token} must not appear in the execution robot"


def test_signal_only_bridge_stays_order_free() -> None:
    """The signal-only bridge package files must remain free of order tokens."""
    from app.strategy_lab import mt5_signal_bridge as sig_bridge

    base = Path(sig_bridge.__file__).resolve().parent
    forbidden = ("order_send", "TRADE_ACTION", "order_modify", "position_close")
    for name in (
        "mt5_signal_bridge.py",
        "run_mt5_signal_bridge.py",
        "signal_store.py",
        "mt5_bridge_manager.py",
    ):
        text = (base / name).read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{token} leaked into signal-only {name}"
