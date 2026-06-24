"""Tests for the Strategy Lab v1.8 MT5 **execution manager** + API endpoints.

These cover the UI control layer for the demo execution robot (config save/list,
dry-run, demo-once, process start/stop/status, logs, history) without a real
MetaTrader 5 terminal: MT5 is replaced by a recorded fake and process management
is exercised with synthetic PIDs so no real robot is ever spawned.

The safety guarantees pinned down here:

    * a dry-run endpoint never sends an order;
    * demo execution is refused without confirmation and on a non-demo account;
    * an unsupported (finalist C) config is refused with a clear message;
    * the manager never starts a duplicate process and never starts a demo run
      without confirmation;
    * no order/trade tokens leak into the signal-only bridge files.
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
from app.strategy_lab import mt5_execution_manager as manager
from app.strategy_lab import mt5_execution_robot as robot

# Reuse the fake + synthetic helpers from the robot test module.
from app.tests.test_mt5_execution_robot import (
    FakeMT5,
    _buy_rates,
    _none_rates,
)

D_PRESET = "D_supertrend_h4_trailing_risk"
C_PRESET = "C_donchian_h1_fixed_atr_risk"


# ---------------------------------------------------------------------------
# Fixtures: isolate the execution state + shared configs dir per test
# ---------------------------------------------------------------------------
@pytest.fixture()
def exec_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    state = tmp_path / "exec_state"
    configs = tmp_path / "configs"
    monkeypatch.setenv("MT5_EXECUTION_ROBOT_DIR", str(state))
    monkeypatch.setenv("MT5_SIGNAL_BRIDGE_CONFIGS_DIR", str(configs))
    return SimpleNamespace(state=state, configs=configs)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def d_config() -> dict:
    return lab_service.export_config(preset_id=D_PRESET)


# ---------------------------------------------------------------------------
# A. Config save / list (shared dir; robot supports only D)
# ---------------------------------------------------------------------------
def test_save_config_creates_json(exec_env: SimpleNamespace, d_config: dict) -> None:
    saved = manager.save_config(d_config, name="exec d config")
    path = Path(saved["path"])
    assert path.exists()
    assert saved["config_summary"]["strategy_id"] == D_PRESET
    assert saved["config_summary"]["is_supported"] is True


def test_save_config_rejects_c(exec_env: SimpleNamespace) -> None:
    c_config = lab_service.export_config(preset_id=C_PRESET)
    with pytest.raises(robot.ExecutionError, match="only D SuperTrend H4"):
        manager.save_config(c_config, name="c")


def test_list_configs_flags_support(
    exec_env: SimpleNamespace, d_config: dict
) -> None:
    manager.save_config(d_config, name="d")
    c_config = lab_service.export_config(preset_id=C_PRESET)
    # Save C via the bridge manager directly (it allows C) so the list has both.
    from app.strategy_lab import mt5_bridge_manager as bm

    bm.save_config(c_config, name="c")

    entries = manager.list_configs()
    by_name = {e["file_name"]: e for e in entries}
    assert by_name["d.json"]["is_supported"] is True
    assert by_name["c.json"]["is_supported"] is False
    assert "only D SuperTrend H4" in by_name["c.json"]["unsupported_reason"]


def test_save_config_endpoint(
    client: TestClient, exec_env: SimpleNamespace, d_config: dict
) -> None:
    resp = client.post(
        "/api/strategy-lab/execution/configs/save", json={"config": d_config}
    )
    assert resp.status_code == 200
    assert Path(resp.json()["path"]).exists()


def test_save_config_endpoint_rejects_c(
    client: TestClient, exec_env: SimpleNamespace
) -> None:
    c_config = lab_service.export_config(preset_id=C_PRESET)
    resp = client.post(
        "/api/strategy-lab/execution/configs/save", json={"config": c_config}
    )
    assert resp.status_code == 422
    assert "only D SuperTrend H4" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# B. Dry-run once (manager + endpoint): never sends an order
# ---------------------------------------------------------------------------
def test_dry_run_once_manager_no_order(
    exec_env: SimpleNamespace, d_config: dict
) -> None:
    fake = FakeMT5(_buy_rates())
    decision = manager.run_dry_run_once(d_config, mt5_loader=lambda: fake)
    assert decision["mode"] == robot.MODE_DRY_RUN
    assert decision["intended_action"] == robot.ACTION_WOULD_OPEN_BUY
    assert fake.sent == []


def test_dry_run_once_endpoint(
    client: TestClient,
    exec_env: SimpleNamespace,
    d_config: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeMT5(_buy_rates())
    monkeypatch.setattr(manager, "_load_mt5", lambda: fake)
    resp = client.post(
        "/api/strategy-lab/execution/dry-run-once", json={"config": d_config}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == robot.MODE_DRY_RUN
    assert body["intended_action"] == robot.ACTION_WOULD_OPEN_BUY
    assert fake.sent == []


def test_dry_run_endpoint_refuses_c(
    client: TestClient, exec_env: SimpleNamespace
) -> None:
    c_config = lab_service.export_config(preset_id=C_PRESET)
    resp = client.post(
        "/api/strategy-lab/execution/dry-run-once", json={"config": c_config}
    )
    assert resp.status_code == 200  # decision-shaped refusal, not an HTTP error
    body = resp.json()
    assert body["mode"] == robot.MODE_REFUSED
    assert body["intended_action"] == robot.ACTION_REFUSED
    assert any("only D SuperTrend H4" in r for r in body["refusal_reasons"])


# ---------------------------------------------------------------------------
# C. Demo once: confirmation + account gating
# ---------------------------------------------------------------------------
def test_demo_once_refused_without_confirmation(
    exec_env: SimpleNamespace, d_config: dict
) -> None:
    fake = FakeMT5(_buy_rates(), trade_mode=FakeMT5.ACCOUNT_TRADE_MODE_DEMO)

    def _loader():
        raise AssertionError("MT5 must not be contacted when unconfirmed")

    decision = manager.run_demo_once(
        d_config, confirm_demo_execution=False, mt5_loader=_loader
    )
    assert decision["mode"] == robot.MODE_REFUSED
    assert robot.REFUSE_NOT_CONFIRMED in decision["refusal_reasons"]
    assert fake.sent == []


def test_demo_once_opens_on_demo(exec_env: SimpleNamespace, d_config: dict) -> None:
    fake = FakeMT5(_buy_rates(), trade_mode=FakeMT5.ACCOUNT_TRADE_MODE_DEMO)
    decision = manager.run_demo_once(
        d_config, confirm_demo_execution=True, mt5_loader=lambda: fake
    )
    assert decision["mode"] == robot.MODE_DEMO_EXECUTION
    assert decision["intended_action"] == robot.ACTION_OPENED_BUY
    assert len(fake.sent) == 1


def test_demo_once_endpoint_refuses_without_confirm(
    client: TestClient, exec_env: SimpleNamespace, d_config: dict
) -> None:
    resp = client.post(
        "/api/strategy-lab/execution/demo-once",
        json={"config": d_config, "confirm_demo_execution": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == robot.MODE_REFUSED
    assert robot.REFUSE_NOT_CONFIRMED in body["refusal_reasons"]


def test_demo_once_endpoint_refuses_live(
    client: TestClient,
    exec_env: SimpleNamespace,
    d_config: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeMT5(_buy_rates(), trade_mode=FakeMT5.ACCOUNT_TRADE_MODE_REAL)
    monkeypatch.setattr(manager, "_load_mt5", lambda: fake)
    resp = client.post(
        "/api/strategy-lab/execution/demo-once",
        json={"config": d_config, "confirm_demo_execution": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == robot.MODE_REFUSED
    assert robot.REFUSE_ACCOUNT_NOT_DEMO in body["refusal_reasons"]
    assert fake.sent == []


# ---------------------------------------------------------------------------
# D. Latest / history / status / logs endpoints (safe when idle)
# ---------------------------------------------------------------------------
def test_status_endpoint_safe_when_idle(
    client: TestClient, exec_env: SimpleNamespace
) -> None:
    resp = client.get("/api/strategy-lab/execution/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["running"] is False
    assert body["latest_execution_decision"] is None


def test_latest_and_history_safe_when_empty(
    client: TestClient, exec_env: SimpleNamespace
) -> None:
    latest = client.get("/api/strategy-lab/execution/latest").json()
    assert latest["latest_execution_decision"] is None

    history = client.get("/api/strategy-lab/execution/history").json()
    assert history["events"] == []
    assert history["count"] == 0


def test_logs_endpoint_safe_when_absent(
    client: TestClient, exec_env: SimpleNamespace
) -> None:
    resp = client.get("/api/strategy-lab/execution/logs?lines=10")
    assert resp.status_code == 200
    body = resp.json()
    assert body["stdout_tail"] == ""
    assert body["stderr_tail"] == ""


def test_history_populated_after_dry_run(
    client: TestClient,
    exec_env: SimpleNamespace,
    d_config: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeMT5(_buy_rates())
    monkeypatch.setattr(manager, "_load_mt5", lambda: fake)
    client.post("/api/strategy-lab/execution/dry-run-once", json={"config": d_config})
    history = client.get("/api/strategy-lab/execution/history").json()
    assert history["count"] == 1
    assert history["events"][0]["mode"] == robot.MODE_DRY_RUN


# ---------------------------------------------------------------------------
# E. Process management: duplicate prevention + unconfirmed demo refused
# ---------------------------------------------------------------------------
def test_start_does_not_start_duplicate(
    exec_env: SimpleNamespace, d_config: dict
) -> None:
    manager.save_config(d_config, name="d")
    config_path = str(exec_env.configs / "d.json")
    manager._write_process_state(
        {
            "pid": os.getpid(),  # this very test process is alive
            "started_at": manager._now_iso(),
            "mode": robot.MODE_DRY_RUN,
            "config_path": config_path,
            "poll_seconds": 60,
            "bars": 500,
            "status": "running",
        }
    )
    result = manager.start_polling(config_path, poll_seconds=60)
    assert result["started"] is False
    assert "already running" in result["message"].lower()
    assert manager.read_process_state()["pid"] == os.getpid()


def test_start_demo_polling_refused_without_confirm(
    exec_env: SimpleNamespace, d_config: dict
) -> None:
    manager.save_config(d_config, name="d")
    config_path = str(exec_env.configs / "d.json")
    result = manager.start_polling(
        config_path,
        poll_seconds=60,
        demo_execution_enabled=True,
        confirm_demo_execution=False,
    )
    assert result["started"] is False
    assert "confirm_demo_execution" in result["message"]
    # No process state was written (nothing started).
    assert manager.read_process_state() == {}


def test_stop_updates_status(exec_env: SimpleNamespace) -> None:
    dead_pid = 2_000_000_000
    manager._write_process_state(
        {
            "pid": dead_pid,
            "started_at": manager._now_iso(),
            "mode": robot.MODE_DRY_RUN,
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


def test_start_endpoint_demo_without_confirm_refused(
    client: TestClient, exec_env: SimpleNamespace, d_config: dict
) -> None:
    manager.save_config(d_config, name="d")
    config_path = str(exec_env.configs / "d.json")
    resp = client.post(
        "/api/strategy-lab/execution/start",
        json={
            "config_path": config_path,
            "demo_execution_enabled": True,
            "confirm_demo_execution": False,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["started"] is False


# ---------------------------------------------------------------------------
# F. Safety: no order/trade tokens leak into the signal-only bridge code
# ---------------------------------------------------------------------------
def test_signal_only_files_remain_order_free() -> None:
    from app.strategy_lab import mt5_signal_bridge as sig_bridge

    base = Path(sig_bridge.__file__).resolve().parent
    forbidden = (
        "order_send",
        "order_check",
        "order_modify",
        "order_close",
        "position_close",
        "TRADE_ACTION",
        "trade_request",
    )
    for name in (
        "mt5_signal_bridge.py",
        "run_mt5_signal_bridge.py",
        "signal_store.py",
        "mt5_bridge_manager.py",
    ):
        text = (base / name).read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{token} leaked into signal-only {name}"


def test_no_sell_short_in_execution_endpoints() -> None:
    from app.api.v1.endpoints import strategy_lab_execution as endpoint

    text = Path(endpoint.__file__).read_text(encoding="utf-8")
    for token in ("ORDER_TYPE_SELL", "POSITION_TYPE_SELL", "position_close"):
        assert token not in text
