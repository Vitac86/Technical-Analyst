"""Tests for the Strategy Lab v1.7 MT5 signal-only bridge.

None of these tests require a real MetaTrader 5 terminal: MT5 access is injected
(``fetch_ohlc_fn``) or mocked with a tiny fake module. The exported v1.6 config
is produced by the real :func:`app.strategy_lab.lab_service.export_config` (which
needs no market data), so the bridge is exercised against the genuine config
shape it will see in production.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.strategy_lab import lab_service, mt5_signal_bridge as bridge
from app.strategy_lab import strategies
from app.strategy_lab.signal_store import SignalStore

D_PRESET = "D_supertrend_h4_trailing_risk"
C_PRESET = "C_donchian_h1_fixed_atr_risk"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------
@pytest.fixture()
def d_config() -> dict:
    """A real exported v1.6 config for finalist D (no market data needed)."""
    return lab_service.export_config(preset_id=D_PRESET)


@pytest.fixture()
def c_config() -> dict:
    """A real exported v1.6 config for finalist C (Donchian H1)."""
    return lab_service.export_config(preset_id=C_PRESET)


@pytest.fixture()
def store(tmp_path: Path) -> SignalStore:
    return SignalStore(tmp_path / "bridge")


# A market context as a live MT5 terminal would supply (read-only specs).
MARKET_CONTEXT = {
    "account_equity": 12500.0,
    "contract_size": 100.0,
    "point_value": 1.0,
    "lot_step": 0.01,
    "spread_points": 25.0,
}


def _donchian_series(n: int = 300) -> pd.DataFrame:
    """A steady XAUUSD-like H1 uptrend that produces Donchian breakouts."""
    times = pd.date_range("2020-01-01", periods=n, freq="1h")  # naive, UTC-like
    close = np.linspace(1800.0, 2200.0, num=n)
    wiggle = np.sin(np.arange(n) / 5.0) * 2.0
    return pd.DataFrame(
        {
            "datetime": times,
            "open": close - wiggle * 0.5,
            "high": close + np.abs(wiggle) + 3.0,
            "low": close - np.abs(wiggle) - 3.0,
            "close": close,
        }
    )


def _supertrend_series(n: int = 400) -> pd.DataFrame:
    """Down-then-up XAUUSD-like H4 series that forces a SuperTrend long flip."""
    times = pd.date_range("2020-01-01", periods=n, freq="4h")  # naive, UTC-like
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
    """Index of the first SuperTrend bullish flip under D's default params."""
    sig = strategies.supertrend_strategy(df, atr_period=10, multiplier=2.0)
    buys = np.where(sig["signal"].to_numpy() == 1)[0]
    assert len(buys), "synthetic series did not produce a bullish flip"
    return int(buys[0])


def _to_mt5_rates(df: pd.DataFrame) -> np.ndarray:
    """Convert an OHLC DataFrame into an MT5-style structured rates array."""
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
        for t, o, h, low, c in zip(
            secs, df["open"], df["high"], df["low"], df["close"]
        )
    ]
    return np.array(rows, dtype=dtype)


class FakeMT5:
    """Minimal stand-in for the MetaTrader5 module (read-only, no execution)."""

    TIMEFRAME_H1 = 16385
    TIMEFRAME_H4 = 16388

    def __init__(self, rates: np.ndarray, symbols: tuple[str, ...] = ("XAUUSDrfd",)):
        self._rates = rates
        self._symbols = symbols

    def initialize(self) -> bool:
        return True

    def shutdown(self) -> None:
        pass

    def last_error(self):
        return (0, "ok")

    def symbol_info(self, name: str):
        if name in self._symbols:
            return SimpleNamespace(name=name, visible=True)
        return None

    def symbol_select(self, name: str, enable: bool) -> bool:  # noqa: FBT001
        return True

    def copy_rates_from_pos(self, symbol: str, timeframe, start: int, count: int):
        return self._rates[-count:] if count else self._rates


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------
def test_valid_config_passes(d_config: dict) -> None:
    bridge.validate_config(d_config)  # should not raise


def test_config_rejects_ml_filter_enabled(d_config: dict) -> None:
    d_config["ml_filter_enabled"] = True
    with pytest.raises(bridge.BridgeError, match="ml_filter_enabled"):
        bridge.validate_config(d_config)


def test_config_rejects_non_long_only(d_config: dict) -> None:
    d_config["direction_mode"] = "both"
    with pytest.raises(bridge.BridgeError, match="long_only"):
        bridge.validate_config(d_config)


def test_config_rejects_unknown_strategy(d_config: dict) -> None:
    d_config["strategy_id"] = "Z_unsupported"
    with pytest.raises(bridge.BridgeError, match="Unsupported strategy_id"):
        bridge.validate_config(d_config)


def test_config_rejects_wrong_timeframe_for_d(d_config: dict) -> None:
    d_config["timeframe"] = "H1"  # D is locked to H4
    with pytest.raises(bridge.BridgeError, match="timeframe must be 'H4'"):
        bridge.validate_config(d_config)


def test_c_config_requires_h1() -> None:
    c_config = lab_service.export_config(preset_id=C_PRESET)
    bridge.validate_config(c_config)  # H1 is correct
    c_config["timeframe"] = "H4"
    with pytest.raises(bridge.BridgeError, match="timeframe must be 'H1'"):
        bridge.validate_config(c_config)


# ---------------------------------------------------------------------------
# Closed-candle rule
# ---------------------------------------------------------------------------
def test_closed_candle_uses_second_to_last_bar() -> None:
    df = _supertrend_series(50)
    closed = bridge.select_closed_candles(df)
    assert len(closed) == len(df) - 1
    # The latest closed candle is the fetched frame's second-to-last bar.
    assert closed["datetime"].iloc[-1] == df["datetime"].iloc[-2]
    # The live (forming) candle is dropped entirely.
    assert df["datetime"].iloc[-1] not in set(closed["datetime"])


def test_select_closed_candles_needs_two_bars() -> None:
    one = _supertrend_series(50).iloc[:1]
    with pytest.raises(bridge.BridgeError):
        bridge.select_closed_candles(one)


# ---------------------------------------------------------------------------
# Signal generation (synthetic data -> expected BUY / NONE)
# ---------------------------------------------------------------------------
def test_synthetic_data_produces_buy_signal(d_config: dict) -> None:
    df = _supertrend_series()
    k = _first_buy_index(df)
    # Fetched frame whose forming bar is k+1, so the latest CLOSED bar is k.
    fetched = df.iloc[: k + 2].reset_index(drop=True)
    closed = bridge.select_closed_candles(fetched)

    record = bridge.build_signal_record(d_config, closed, symbol="XAUUSD")
    assert record["signal_type"] == "BUY"
    assert record["reason"] == "supertrend_flip_bullish"
    assert record["signal_time"] == pd.Timestamp(df["datetime"].iloc[k]).isoformat()
    assert record["close_price"] is not None
    assert record["atr_value"] is not None
    assert record["suggested_entry_reference"] == "next_bar_open_or_market"


def test_synthetic_data_produces_none_signal(d_config: dict) -> None:
    df = _supertrend_series()
    k = _first_buy_index(df)
    # Latest closed bar is k-1 (the bar just before the flip) -> no entry.
    fetched = df.iloc[: k + 1].reset_index(drop=True)
    closed = bridge.select_closed_candles(fetched)

    record = bridge.build_signal_record(d_config, closed, symbol="XAUUSD")
    assert record["signal_type"] == "NONE"


# ---------------------------------------------------------------------------
# Safety: execution is always disabled, no order functions referenced
# ---------------------------------------------------------------------------
def test_execution_enabled_constant_is_false() -> None:
    assert bridge.EXECUTION_ENABLED is False
    bridge.assert_signal_only()  # must not raise while the lock holds


def test_record_marks_execution_disabled(d_config: dict) -> None:
    df = _supertrend_series()
    k = _first_buy_index(df)
    closed = bridge.select_closed_candles(df.iloc[: k + 2].reset_index(drop=True))
    record = bridge.build_signal_record(d_config, closed, symbol="XAUUSD")
    assert record["execution_enabled"] is False
    assert record["status"] == "signal_only"


def test_no_order_execution_functions_referenced() -> None:
    """No trade/order-mutating MT5 calls appear anywhere in the bridge package."""
    forbidden = (
        "order_send",
        "order_check",
        "order_modify",
        "order_close",
        "position_close",
        "positions_close",
        "TRADE_ACTION",
        "trade_request",
    )
    base = Path(bridge.__file__).resolve().parent
    sources = [
        base / "mt5_signal_bridge.py",
        base / "run_mt5_signal_bridge.py",
        base / "signal_store.py",
    ]
    for path in sources:
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{token} must not appear in {path.name}"


# ---------------------------------------------------------------------------
# Signal store: logging + one-signal-per-candle
# ---------------------------------------------------------------------------
def test_store_writes_log_and_latest(d_config: dict, store: SignalStore) -> None:
    df = _supertrend_series()
    k = _first_buy_index(df)
    closed = bridge.select_closed_candles(df.iloc[: k + 2].reset_index(drop=True))
    record = bridge.build_signal_record(d_config, closed, symbol="XAUUSD")

    key = store.make_key(record["strategy_id"], record["symbol"], record["timeframe"])
    store.record(key, record)

    assert store.signals_path.exists()
    assert store.latest_path.exists()
    assert store.state_path.exists()

    latest = store.read_latest()
    assert latest["signal_id"] == record["signal_id"]
    assert latest["execution_enabled"] is False

    history = store.read_history()
    assert len(history) == 1
    assert history[0]["signal_type"] == "BUY"


def test_duplicate_signal_prevention(d_config: dict, store: SignalStore) -> None:
    df = _supertrend_series()
    k = _first_buy_index(df)
    fetched = df.iloc[: k + 2].reset_index(drop=True)

    def fetch_ohlc_fn(symbol: str, timeframe: str, bars: int) -> pd.DataFrame:
        return fetched

    first = bridge.run_once(
        d_config, store, symbol="XAUUSD", fetch_ohlc_fn=fetch_ohlc_fn
    )
    second = bridge.run_once(
        d_config, store, symbol="XAUUSD", fetch_ohlc_fn=fetch_ohlc_fn
    )

    assert first is not None and first["signal_type"] == "BUY"
    assert second is None, "the same closed candle must not emit a second signal"
    assert len(store.read_history()) == 1  # only one row logged


def test_already_processed_helper(store: SignalStore) -> None:
    key = store.make_key(D_PRESET, "XAUUSD", "H4")
    t0 = pd.Timestamp("2024-01-01T00:00:00")
    assert store.already_processed(key, t0) is False
    store.record(
        key,
        {
            "signal_id": "x",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "symbol": "XAUUSD",
            "timeframe": "H4",
            "strategy_id": D_PRESET,
            "signal_time": t0.isoformat(),
            "signal_type": "NONE",
            "reason": "no_entry",
            "close_price": 2000.0,
            "atr_value": 10.0,
            "suggested_entry_reference": "next_bar_open_or_market",
            "risk_percent": 1.0,
            "initial_stop_loss_atr": 2.5,
            "trailing_stop_atr": 6.0,
            "take_profit_atr": None,
            "status": "signal_only",
            "execution_enabled": False,
        },
    )
    assert store.already_processed(key, t0) is True  # same candle
    assert store.already_processed(key, t0 + pd.Timedelta(hours=4)) is False  # newer


# ---------------------------------------------------------------------------
# Mocked MT5: rates conversion + end-to-end fetch
# ---------------------------------------------------------------------------
def test_rates_to_dataframe_shape() -> None:
    df = _supertrend_series(20)
    rates = _to_mt5_rates(df)
    out = bridge.rates_to_dataframe(rates)
    assert list(out.columns) == list(bridge.RATES_COLUMNS)
    assert out["datetime"].iloc[0] == df["datetime"].iloc[0]
    assert pytest.approx(out["close"].iloc[-1]) == df["close"].iloc[-1]


def test_resolve_symbol_falls_back_to_rfd() -> None:
    fake = FakeMT5(_to_mt5_rates(_supertrend_series(20)))
    # Config says XAUUSD but the broker only exposes XAUUSDrfd.
    assert bridge.resolve_symbol(fake, "XAUUSD") == "XAUUSDrfd"


def test_run_once_with_mocked_mt5(d_config: dict, store: SignalStore) -> None:
    df = _supertrend_series()
    k = _first_buy_index(df)
    fetched = df.iloc[: k + 2].reset_index(drop=True)
    fake = FakeMT5(_to_mt5_rates(fetched))

    fetch_ohlc_fn = bridge.make_fetch_ohlc_fn(fake)
    record = bridge.run_once(
        d_config,
        store,
        symbol="XAUUSDrfd",
        fetch_ohlc_fn=fetch_ohlc_fn,
        bars=len(fetched),
        generated_at=datetime.now(timezone.utc),
    )
    assert record is not None
    assert record["signal_type"] == "BUY"
    assert record["symbol"] == "XAUUSDrfd"


def test_missing_mt5_package_message(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "MetaTrader5":
            raise ImportError("no module")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(bridge.BridgeError, match="pip install MetaTrader5"):
        bridge.load_mt5()


# ---------------------------------------------------------------------------
# v1.7.2: enriched trading plan (signal-only reference, never an order)
# ---------------------------------------------------------------------------
def _buy_record(d_config: dict) -> dict:
    df = _supertrend_series()
    k = _first_buy_index(df)
    closed = bridge.select_closed_candles(df.iloc[: k + 2].reset_index(drop=True))
    return bridge.build_signal_record(
        d_config, closed, symbol="XAUUSD", market_context=MARKET_CONTEXT
    )


def _none_record(d_config: dict) -> dict:
    df = _supertrend_series()
    k = _first_buy_index(df)
    closed = bridge.select_closed_candles(df.iloc[: k + 1].reset_index(drop=True))
    return bridge.build_signal_record(
        d_config, closed, symbol="XAUUSD", market_context=MARKET_CONTEXT
    )


def test_enriched_buy_has_trading_plan(d_config: dict) -> None:
    rec = _buy_record(d_config)
    assert rec["execution_enabled"] is False
    assert rec["status"] == "signal_only"

    plan = rec["trading_plan"]
    assert plan["reference_entry_type"] == bridge.REFERENCE_ENTRY_TYPE
    assert plan["reference_entry_price"] == rec["close_price"]
    assert plan["initial_stop_price"] < plan["reference_entry_price"]
    assert plan["trailing_stop_reference"] < rec["close_price"]
    assert plan["risk_per_unit"] > 0
    assert plan["risk_amount"] == pytest.approx(12500.0 * 0.01)
    assert plan["suggested_lot"] is not None and plan["suggested_lot"] > 0
    assert plan["account_equity_reference"] == 12500.0
    assert plan["account_equity_source"] == "mt5_account_equity"
    assert "signal-only" in plan["notes"].lower()

    snapshot = rec["market_snapshot"]
    assert snapshot["spread_points"] == 25.0
    assert snapshot["latest_closed_candle_time"] == rec["signal_time"]
    assert snapshot["previous_closed_candle_time"] is not None

    state = rec["strategy_state"]
    assert state["strategy_regime"] == "bullish"
    assert state["is_new_long_signal"] is True
    assert state["bars_since_last_long_signal"] == 0
    assert state["supertrend_value"] is not None


def test_enriched_none_has_plan_without_fake_entry(d_config: dict) -> None:
    rec = _none_record(d_config)
    assert rec["signal_type"] == "NONE"
    assert rec["execution_enabled"] is False

    plan = rec["trading_plan"]
    assert plan is not None
    # No fabricated entry / stop / size on a no-entry candle.
    assert plan["reference_entry_type"] is None
    assert plan["reference_entry_price"] is None
    assert plan["initial_stop_price"] is None
    assert plan["take_profit_price"] is None
    assert plan["risk_per_unit"] is None
    assert plan["risk_amount"] is None
    assert plan["suggested_lot"] is None
    # ... but it still explains why and what the next BUY needs.
    assert plan["reason_human"]
    assert plan["next_condition"]


def test_none_trailing_reference_only_when_bullish(d_config: dict) -> None:
    """A NONE only shows a trailing reference when already in a bullish regime."""
    rec = _none_record(d_config)
    plan = rec["trading_plan"]
    if rec["strategy_state"]["strategy_regime"] == "bullish":
        assert plan["trailing_stop_reference"] is not None
    else:
        assert plan["trailing_stop_reference"] is None


def test_suggested_lot_falls_back_to_config_equity(d_config: dict) -> None:
    df = _supertrend_series()
    k = _first_buy_index(df)
    closed = bridge.select_closed_candles(df.iloc[: k + 2].reset_index(drop=True))
    rec = bridge.build_signal_record(d_config, closed, symbol="XAUUSD")  # no MT5 ctx
    plan = rec["trading_plan"]
    assert plan["account_equity_source"] == "config_initial_equity"
    assert plan["account_equity_reference"] == d_config["risk_parameters"]["initial_equity"]
    assert plan["suggested_lot"] is not None


def test_donchian_diagnostics_present(c_config: dict) -> None:
    closed = bridge.select_closed_candles(_donchian_series())
    rec = bridge.build_signal_record(
        c_config, closed, symbol="XAUUSD", market_context=MARKET_CONTEXT
    )
    state = rec["strategy_state"]
    assert state["donchian_high"] is not None
    assert state["donchian_low"] is not None
    assert state["supertrend_value"] is None  # Donchian has no SuperTrend line
    assert rec["strategy_regime"] in {"bullish", "bearish", "neutral", "unknown"}
    # Donchian uses a fixed stop -> no trailing reference is fabricated.
    assert rec["trading_plan"]["trailing_stop_reference"] is None


def test_enriched_record_serializes_without_execution_tokens(d_config: dict) -> None:
    """The enriched record/plan must not introduce any order-execution tokens."""
    import json

    forbidden = (
        "order_send",
        "order_modify",
        "order_close",
        "position_close",
        "TRADE_ACTION",
        "trade_request",
    )
    blob = json.dumps(_buy_record(d_config)) + json.dumps(_none_record(d_config))
    for token in forbidden:
        assert token not in blob


# ---------------------------------------------------------------------------
# v1.7.2: recent-candle diagnostics (multiple rows, no duplicate signals)
# ---------------------------------------------------------------------------
def test_recent_checks_multiple_rows_no_duplicate_signal(
    d_config: dict, store: SignalStore
) -> None:
    df = _supertrend_series()
    k = _first_buy_index(df)
    fetched = df.iloc[: k + 2].reset_index(drop=True)

    def fetch_ohlc_fn(symbol: str, timeframe: str, bars: int) -> pd.DataFrame:
        return fetched

    first = bridge.run_once(
        d_config, store, symbol="XAUUSD", fetch_ohlc_fn=fetch_ohlc_fn, recent_limit=10
    )
    second = bridge.run_once(
        d_config, store, symbol="XAUUSD", fetch_ohlc_fn=fetch_ohlc_fn, recent_limit=10
    )

    # One official signal per closed candle, even though diagnostics re-run.
    assert first is not None and first["signal_type"] == "BUY"
    assert second is None
    assert len(store.read_history()) == 1

    checks = store.read_recent_checks()["checks"]
    assert len(checks) > 1  # diagnostics over several candles
    assert all(row["execution_enabled"] is False for row in checks)
    assert checks[0]["signal_time"] >= checks[-1]["signal_time"]  # newest first
    # Exactly one of the recent rows is a long signal (the latest closed candle).
    assert sum(1 for row in checks if row["is_long_signal"]) == 1


def test_recent_checks_limit_is_capped(d_config: dict) -> None:
    closed = bridge.select_closed_candles(_supertrend_series())
    rows = bridge.build_recent_checks(d_config, closed, limit=10_000)
    assert len(rows) <= bridge.MAX_RECENT_LIMIT


def test_csv_history_flattens_enriched_fields(d_config: dict, store: SignalStore) -> None:
    rec = _buy_record(d_config)
    key = store.make_key(rec["strategy_id"], rec["symbol"], rec["timeframe"])
    store.record(key, rec)
    row = store.read_history()[0]
    for column in (
        "strategy_regime",
        "reference_entry_price",
        "initial_stop_price",
        "trailing_stop_reference",
        "take_profit_price",
        "suggested_lot",
    ):
        assert column in row
    assert row["signal_type"] == "BUY"
    assert row["execution_enabled"] == "False"  # CSV stringifies the bool
