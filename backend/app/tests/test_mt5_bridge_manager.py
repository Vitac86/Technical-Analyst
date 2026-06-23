"""Tests for the Strategy Lab v1.7.1 MT5 signal-only **bridge manager** + API.

These cover the new UI control layer (config save/list, MT5 readiness, run-once,
process start/stop/status, logs) without a real MetaTrader 5 terminal: MT5 is
replaced by a small read-only fake, and process management is exercised with
synthetic PIDs so no real poller is ever spawned in the unit tests.

Everything here must remain signal-only: a dedicated test asserts that no
order/trade-mutating tokens leak into the new manager or endpoints.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.strategy_lab import lab_service
from app.strategy_lab import mt5_bridge_manager as manager
from app.strategy_lab import mt5_signal_bridge as bridge
from app.strategy_lab import strategies

D_PRESET = "D_supertrend_h4_trailing_risk"


# ---------------------------------------------------------------------------
# Fixtures: isolate the state + configs directories per test via env overrides
# ---------------------------------------------------------------------------
@pytest.fixture()
def bridge_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    state = tmp_path / "state"
    configs = tmp_path / "configs"
    monkeypatch.setenv("MT5_SIGNAL_BRIDGE_DIR", str(state))
    monkeypatch.setenv("MT5_SIGNAL_BRIDGE_CONFIGS_DIR", str(configs))
    return SimpleNamespace(state=state, configs=configs)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def d_config() -> dict:
    """A real exported v1.6 config for finalist D (no market data needed)."""
    return lab_service.export_config(preset_id=D_PRESET)


# ---------------------------------------------------------------------------
# Synthetic data + a read-only fake MT5 with terminal/account info
# ---------------------------------------------------------------------------
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


class FakeMT5:
    """Read-only MetaTrader5 stand-in: terminal/account/symbol/rates, no trading."""

    TIMEFRAME_H1 = 16385
    TIMEFRAME_H4 = 16388

    def __init__(
        self,
        rates: np.ndarray,
        *,
        symbols: tuple[str, ...] = ("XAUUSDrfd",),
        terminal: bool = True,
        account: bool = True,
    ) -> None:
        self._rates = rates
        self._symbols = symbols
        self._terminal = terminal
        self._account = account

    def initialize(self) -> bool:
        return True

    def shutdown(self) -> None:
        pass

    def last_error(self):
        return (0, "ok")

    def terminal_info(self):
        return SimpleNamespace(connected=True) if self._terminal else None

    def account_info(self):
        return SimpleNamespace(login=0, currency="USD") if self._account else None

    def symbol_info(self, name: str):
        if name in self._symbols:
            return SimpleNamespace(name=name, visible=True)
        return None

    def symbol_select(self, name: str, enable: bool) -> bool:  # noqa: FBT001
        return True

    def copy_rates_from_pos(self, symbol: str, timeframe, start: int, count: int):
        return self._rates[-count:] if count else self._rates


def _buy_fake() -> FakeMT5:
    """A fake whose latest CLOSED candle is a SuperTrend bullish flip (BUY)."""
    df = _supertrend_series()
    k = _first_buy_index(df)
    fetched = df.iloc[: k + 2].reset_index(drop=True)  # forming=k+1, closed last=k
    return FakeMT5(_to_mt5_rates(fetched))


# ---------------------------------------------------------------------------
# A. Config save / list
# ---------------------------------------------------------------------------
def test_save_config_creates_json(bridge_env: SimpleNamespace, d_config: dict) -> None:
    saved = manager.save_config(d_config, name="my bridge config")
    path = Path(saved["path"])
    assert path.exists()
    assert path.parent == bridge_env.configs.resolve()
    assert saved["file_name"] == "my_bridge_config.json"
    assert saved["config_summary"]["strategy_id"] == D_PRESET
    assert saved["config_summary"]["ml_filter_enabled"] is False


def test_save_config_endpoint_creates_json(
    client: TestClient, bridge_env: SimpleNamespace, d_config: dict
) -> None:
    resp = client.post(
        "/api/strategy-lab/signals/configs/save", json={"config": d_config}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["execution_enabled"] is False
    assert Path(body["path"]).exists()


def test_save_config_rejects_ml_enabled(
    client: TestClient, bridge_env: SimpleNamespace, d_config: dict
) -> None:
    d_config["ml_filter_enabled"] = True
    resp = client.post(
        "/api/strategy-lab/signals/configs/save", json={"config": d_config}
    )
    assert resp.status_code == 422
    assert "ml_filter_enabled" in resp.json()["detail"]


def test_list_configs_endpoint_returns_saved_config(
    client: TestClient, bridge_env: SimpleNamespace, d_config: dict
) -> None:
    manager.save_config(d_config, name="d_default")
    resp = client.get("/api/strategy-lab/signals/configs")
    assert resp.status_code == 200
    configs = resp.json()["configs"]
    assert len(configs) == 1
    entry = configs[0]
    assert entry["file_name"] == "d_default.json"
    assert entry["strategy_id"] == D_PRESET
    assert entry["timeframe"] == "H4"
    assert entry["ml_filter_enabled"] is False
    assert entry["created_at"] is not None
    assert entry["modified_at"] is not None


# ---------------------------------------------------------------------------
# B. Config path containment (no arbitrary file reads)
# ---------------------------------------------------------------------------
def test_resolve_config_path_rejects_outside_dir(
    bridge_env: SimpleNamespace, tmp_path: Path
) -> None:
    outside = tmp_path / "evil.json"
    outside.write_text("{}", encoding="utf-8")
    with pytest.raises(bridge.BridgeError, match="inside the MetaTrader_Data/configs"):
        manager.resolve_config_path(str(outside))


# ---------------------------------------------------------------------------
# C. MT5 readiness (mocked MT5)
# ---------------------------------------------------------------------------
def test_mt5_readiness_ok(
    bridge_env: SimpleNamespace, d_config: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeMT5(_to_mt5_rates(_supertrend_series(400)))
    monkeypatch.setattr(manager, "_load_mt5", lambda: fake)
    result = manager.check_mt5_readiness(d_config, bars=400)
    assert result["status"] == "ok"
    assert result["mt5_package_available"] is True
    assert result["terminal_connected"] is True
    assert result["account_connected"] is True
    assert result["symbol"] == "XAUUSDrfd"
    assert result["timeframe"] == "H4"
    assert result["rates_available"] is True
    assert result["latest_closed_candle_time"] is not None
    assert result["execution_enabled"] is False


def test_mt5_readiness_missing_package(
    bridge_env: SimpleNamespace, d_config: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise() -> object:
        raise bridge.BridgeError("pip install MetaTrader5")

    monkeypatch.setattr(manager, "_load_mt5", _raise)
    result = manager.check_mt5_readiness(d_config)
    assert result["status"] == "error"
    assert result["mt5_package_available"] is False
    assert "MetaTrader5" in result["message"]


def test_mt5_readiness_endpoint(
    client: TestClient,
    bridge_env: SimpleNamespace,
    d_config: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeMT5(_to_mt5_rates(_supertrend_series(400)))
    monkeypatch.setattr(manager, "_load_mt5", lambda: fake)
    resp = client.post(
        "/api/strategy-lab/signals/mt5-check", json={"config": d_config, "bars": 400}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# D. Run once (mocked MT5 / bridge core)
# ---------------------------------------------------------------------------
def test_check_once_emits_buy(
    bridge_env: SimpleNamespace, d_config: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(manager, "_load_mt5", _buy_fake)
    result = manager.run_check_once(d_config, bars=500)
    assert result["emitted"] is True
    assert result["signal"]["signal_type"] == "BUY"
    assert result["execution_enabled"] is False
    # Persisted through the existing store.
    assert (bridge_env.state / "latest_signal.json").exists()
    assert (bridge_env.state / "signals.csv").exists()


def test_check_once_endpoint(
    client: TestClient,
    bridge_env: SimpleNamespace,
    d_config: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(manager, "_load_mt5", _buy_fake)
    resp = client.post(
        "/api/strategy-lab/signals/check-once", json={"config": d_config}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["signal"]["signal_type"] == "BUY"
    assert body["execution_enabled"] is False


def test_check_once_endpoint_surfaces_mt5_error(
    client: TestClient,
    bridge_env: SimpleNamespace,
    d_config: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise() -> object:
        raise bridge.BridgeError("pip install MetaTrader5")

    monkeypatch.setattr(manager, "_load_mt5", _raise)
    resp = client.post(
        "/api/strategy-lab/signals/check-once", json={"config": d_config}
    )
    assert resp.status_code == 200  # actionable error surfaced inline
    body = resp.json()
    assert body["ok"] is False
    assert body["emitted"] is False
    assert "MetaTrader5" in body["stderr"]
    assert body["execution_enabled"] is False


# ---------------------------------------------------------------------------
# E/F/G. Process management: duplicate prevention, stop, status
# ---------------------------------------------------------------------------
def test_start_does_not_start_duplicate(
    bridge_env: SimpleNamespace, d_config: dict
) -> None:
    manager.save_config(d_config, name="d")
    config_path = str(bridge_env.configs / "d.json")
    # Pretend a live poller already exists (this very test process is alive).
    manager._write_process_state(
        {
            "pid": os.getpid(),
            "started_at": manager._now_iso(),
            "config_path": config_path,
            "poll_seconds": 60,
            "bars": 500,
            "status": "running",
        }
    )
    result = manager.start_polling(config_path, poll_seconds=60)
    assert result["started"] is False
    assert result["running"] is True
    assert "already running" in result["message"].lower()
    # The recorded PID is untouched (no second process started).
    assert manager.read_process_state()["pid"] == os.getpid()


def test_stop_updates_status(bridge_env: SimpleNamespace) -> None:
    dead_pid = 2_000_000_000  # astronomically unlikely to exist
    manager._write_process_state(
        {
            "pid": dead_pid,
            "started_at": manager._now_iso(),
            "config_path": "x",
            "poll_seconds": 60,
            "bars": 500,
            "status": "running",
        }
    )
    result = manager.stop_polling()
    assert result["stopped"] is True
    assert result["running"] is False
    assert manager.read_process_state()["status"] == "stopped"
    status = manager.process_status()
    assert status["running"] is False
    assert status["status"] == "stopped"


def test_status_endpoint_safe_when_idle(
    client: TestClient, bridge_env: SimpleNamespace
) -> None:
    resp = client.get("/api/strategy-lab/signals/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["running"] is False
    assert body["execution_enabled"] is False
    assert body["latest_signal"] is None


# ---------------------------------------------------------------------------
# H. Logs + read-only latest/history endpoints
# ---------------------------------------------------------------------------
def test_logs_endpoint_safe_when_absent(
    client: TestClient, bridge_env: SimpleNamespace
) -> None:
    resp = client.get("/api/strategy-lab/signals/logs?lines=10")
    assert resp.status_code == 200
    body = resp.json()
    assert body["stdout_tail"] == ""
    assert body["stderr_tail"] == ""
    assert body["execution_enabled"] is False


def test_latest_and_history_endpoints_safe(
    client: TestClient, bridge_env: SimpleNamespace
) -> None:
    latest = client.get("/api/strategy-lab/signals/latest").json()
    assert latest["signal"] is None
    assert latest["execution_enabled"] is False

    history = client.get("/api/strategy-lab/signals/history").json()
    assert history["signals"] == []
    assert history["count"] == 0
    assert history["execution_enabled"] is False


# ---------------------------------------------------------------------------
# Safety: no order/trade tokens in the new manager or endpoints
# ---------------------------------------------------------------------------
def test_no_execution_tokens_in_new_code() -> None:
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
    manager_path = Path(manager.__file__).resolve()
    endpoint_path = (
        manager_path.parents[1]
        / "api"
        / "v1"
        / "endpoints"
        / "strategy_lab_signals.py"
    )
    for path in (manager_path, endpoint_path):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{token} must not appear in {path.name}"


def test_execution_lock_intact() -> None:
    assert bridge.EXECUTION_ENABLED is False
    bridge.assert_signal_only()
