"""Tests for Strategy Lab v1.9 position-sizing modes on the demo execution robot.

These pin down the three sizing modes added in v1.9 without a real MT5 terminal:

    * ``risk_percent_auto``        -- unchanged v1.8 risk-percent sizing;
    * ``fixed_lot_manual``         -- size from a user lot, report implied risk;
    * ``risk_percent_with_max_lot``-- auto risk-percent sizing capped by max lot.

The demo-only safety gates are unchanged: a manual lot that implies more than the
configured risk ceiling is *allowed in dry-run* (so the operator sees it) but
*refused in demo execution* unless ``allow_high_manual_risk`` is set.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.strategy_lab import lab_service
from app.strategy_lab import mt5_execution_manager as manager
from app.strategy_lab import mt5_execution_robot as robot
from app.strategy_lab import run_mt5_execution_robot as cli
from app.strategy_lab.execution_store import ExecutionStore

# Reuse the fake MT5 + synthetic rates from the robot test module.
from app.tests.test_mt5_execution_robot import FakeMT5, _buy_rates

D_PRESET = "D_supertrend_h4_trailing_risk"

# A spec with simple round numbers so the lot math is exact.
SPEC = {
    "contract_size": 100.0,
    "volume_min": 0.01,
    "volume_max": 50.0,
    "volume_step": 0.01,
}

# Shared sizing inputs: risk_per_unit = 2.5 * 10 = 25; auto raw_lot = 0.04.
BASE = dict(
    equity=10_000.0,
    risk_percent=1.0,
    entry_price=2000.0,
    atr_value=10.0,
    initial_stop_loss_atr=2.5,
    free_margin=100_000.0,
    required_margin=50.0,
    allow_min_lot_rounding=False,
)


@pytest.fixture()
def d_config() -> dict:
    return lab_service.export_config(preset_id=D_PRESET)


@pytest.fixture()
def store(tmp_path: Path) -> ExecutionStore:
    return ExecutionStore(tmp_path / "exec")


# ---------------------------------------------------------------------------
# compute_sizing: risk_percent_auto stays exactly as v1.8
# ---------------------------------------------------------------------------
def test_risk_percent_auto_unchanged() -> None:
    sizing = robot.compute_sizing(spec=SPEC, **BASE)
    assert sizing["execution_sizing_mode"] == robot.SIZING_MODE_RISK_PERCENT_AUTO
    assert sizing["raw_lot"] == pytest.approx(0.04)
    assert sizing["rounded_lot"] == pytest.approx(0.04)
    assert sizing["final_lot"] == pytest.approx(0.04)
    assert sizing["sizing_status"] == robot.SIZING_OK
    # Risk reported as final_* for auto modes; implied_* stays unset.
    assert sizing["final_risk_amount"] == pytest.approx(100.0)
    assert sizing["final_risk_percent"] == pytest.approx(1.0)
    assert sizing["implied_risk_amount"] is None
    assert sizing["capped_by_max_lot"] is False


# ---------------------------------------------------------------------------
# fixed_lot_manual: uses manual_lot, not risk_percent
# ---------------------------------------------------------------------------
def test_fixed_lot_manual_uses_manual_lot_not_risk_percent() -> None:
    sizing = robot.compute_sizing(
        spec=SPEC,
        execution_sizing_mode=robot.SIZING_MODE_FIXED_LOT_MANUAL,
        manual_lot=0.05,  # auto would have been 0.04 -> proves manual wins
        **BASE,
    )
    assert sizing["execution_sizing_mode"] == robot.SIZING_MODE_FIXED_LOT_MANUAL
    assert sizing["manual_lot_requested"] == pytest.approx(0.05)
    assert sizing["rounded_lot"] == pytest.approx(0.05)
    assert sizing["final_lot"] == pytest.approx(0.05)
    # risk_percent was NOT used to size: no risk_amount was computed from it.
    assert sizing["risk_amount"] is None
    assert sizing["sizing_status"] == robot.SIZING_OK


def test_fixed_lot_manual_reports_implied_risk() -> None:
    sizing = robot.compute_sizing(
        spec=SPEC,
        execution_sizing_mode=robot.SIZING_MODE_FIXED_LOT_MANUAL,
        manual_lot=0.05,
        **BASE,
    )
    # implied = risk_per_unit(25) * contract(100) * lot(0.05) = 125 -> 1.25% of 10k.
    assert sizing["implied_risk_amount"] == pytest.approx(125.0)
    assert sizing["implied_risk_percent"] == pytest.approx(1.25)


def test_fixed_lot_manual_rounds_to_step_with_warning() -> None:
    sizing = robot.compute_sizing(
        spec=SPEC,
        execution_sizing_mode=robot.SIZING_MODE_FIXED_LOT_MANUAL,
        manual_lot=0.057,  # not on the 0.01 step
        **BASE,
    )
    assert sizing["rounded_lot"] == pytest.approx(0.05)
    assert robot.WARN_MANUAL_LOT_ROUNDED in sizing["sizing_warnings"]


def test_fixed_lot_manual_below_min_refused() -> None:
    spec = {**SPEC, "volume_min": 0.10}
    sizing = robot.compute_sizing(
        spec=spec,
        execution_sizing_mode=robot.SIZING_MODE_FIXED_LOT_MANUAL,
        manual_lot=0.05,  # below the 0.10 minimum
        **BASE,
    )
    assert sizing["sizing_status"] == robot.SIZING_LOT_BELOW_MIN


def test_fixed_lot_manual_above_max_refused() -> None:
    spec = {**SPEC, "volume_max": 0.10}
    sizing = robot.compute_sizing(
        spec=spec,
        execution_sizing_mode=robot.SIZING_MODE_FIXED_LOT_MANUAL,
        manual_lot=0.50,  # above the 0.10 maximum
        **BASE,
    )
    assert sizing["sizing_status"] == robot.SIZING_LOT_ABOVE_MAX


def test_fixed_lot_manual_missing_lot_refused() -> None:
    sizing = robot.compute_sizing(
        spec=SPEC,
        execution_sizing_mode=robot.SIZING_MODE_FIXED_LOT_MANUAL,
        manual_lot=None,
        **BASE,
    )
    assert sizing["sizing_status"] == robot.SIZING_MANUAL_LOT_REQUIRED


def test_fixed_lot_manual_high_risk_is_warning_status() -> None:
    sizing = robot.compute_sizing(
        spec=SPEC,
        execution_sizing_mode=robot.SIZING_MODE_FIXED_LOT_MANUAL,
        manual_lot=0.20,  # implied 5% > default ceiling 3%
        max_manual_risk_percent=3.0,
        **BASE,
    )
    assert sizing["implied_risk_percent"] == pytest.approx(5.0)
    assert sizing["sizing_status"] == robot.SIZING_WARNING_MANUAL_RISK_TOO_HIGH
    assert robot.WARN_MANUAL_RISK_HIGH in sizing["sizing_warnings"]


# ---------------------------------------------------------------------------
# risk_percent_with_max_lot: cap the auto lot
# ---------------------------------------------------------------------------
def test_risk_percent_with_max_lot_caps() -> None:
    sizing = robot.compute_sizing(
        spec=SPEC,
        execution_sizing_mode=robot.SIZING_MODE_RISK_PERCENT_WITH_MAX_LOT,
        max_lot=0.02,  # auto is 0.04 -> capped to 0.02
        **BASE,
    )
    assert sizing["auto_lot_before_cap"] == pytest.approx(0.04)
    assert sizing["max_lot"] == pytest.approx(0.02)
    assert sizing["final_lot"] == pytest.approx(0.02)
    assert sizing["capped_by_max_lot"] is True
    assert robot.WARN_CAPPED_BY_MAX_LOT in sizing["sizing_warnings"]
    # final risk reflects the capped lot: 25 * 100 * 0.02 = 50 -> 0.5%.
    assert sizing["final_risk_amount"] == pytest.approx(50.0)
    assert sizing["final_risk_percent"] == pytest.approx(0.5)


def test_risk_percent_with_max_lot_not_binding() -> None:
    sizing = robot.compute_sizing(
        spec=SPEC,
        execution_sizing_mode=robot.SIZING_MODE_RISK_PERCENT_WITH_MAX_LOT,
        max_lot=0.10,  # above auto 0.04 -> no cap
        **BASE,
    )
    assert sizing["final_lot"] == pytest.approx(0.04)
    assert sizing["capped_by_max_lot"] is False


# ---------------------------------------------------------------------------
# run_once: dry-run warns; demo refuses high manual risk unless allowed
# ---------------------------------------------------------------------------
def test_dry_run_high_manual_risk_warns_no_order(
    d_config: dict, store: ExecutionStore
) -> None:
    fake = FakeMT5(_buy_rates(), trade_mode=FakeMT5.ACCOUNT_TRADE_MODE_DEMO)
    decision = robot.run_once(
        d_config,
        store,
        fake,
        bars=500,
        execution_sizing_mode=robot.SIZING_MODE_FIXED_LOT_MANUAL,
        manual_lot=0.05,
        max_manual_risk_percent=0.0001,  # force the warning regardless of inputs
    )
    assert decision["mode"] == robot.MODE_DRY_RUN
    assert decision["intended_action"] == robot.ACTION_WOULD_OPEN_BUY
    assert decision["sizing"]["sizing_status"] == robot.SIZING_WARNING_MANUAL_RISK_TOO_HIGH
    assert fake.sent == []  # dry-run never sends


def test_demo_high_manual_risk_refused_without_allow(
    d_config: dict, store: ExecutionStore
) -> None:
    fake = FakeMT5(_buy_rates(), trade_mode=FakeMT5.ACCOUNT_TRADE_MODE_DEMO)
    decision = robot.run_once(
        d_config,
        store,
        fake,
        bars=500,
        execution_enabled=True,
        confirm_demo_execution=True,
        execution_sizing_mode=robot.SIZING_MODE_FIXED_LOT_MANUAL,
        manual_lot=0.05,
        max_manual_risk_percent=0.0001,
        allow_high_manual_risk=False,
    )
    assert decision["intended_action"] == robot.ACTION_REFUSED
    assert robot.REFUSE_MANUAL_RISK_TOO_HIGH in decision["refusal_reasons"]
    assert fake.sent == []


def test_demo_high_manual_risk_allowed_opens_buy(
    d_config: dict, store: ExecutionStore
) -> None:
    fake = FakeMT5(_buy_rates(), trade_mode=FakeMT5.ACCOUNT_TRADE_MODE_DEMO)
    decision = robot.run_once(
        d_config,
        store,
        fake,
        bars=500,
        execution_enabled=True,
        confirm_demo_execution=True,
        execution_sizing_mode=robot.SIZING_MODE_FIXED_LOT_MANUAL,
        manual_lot=0.05,
        max_manual_risk_percent=0.0001,
        allow_high_manual_risk=True,
    )
    assert decision["intended_action"] == robot.ACTION_OPENED_BUY
    assert len(fake.sent) == 1
    # The order uses the MANUAL lot, not a risk-percent lot.
    assert fake.sent[0]["volume"] == pytest.approx(0.05)
    assert decision["sizing"]["execution_sizing_mode"] == robot.SIZING_MODE_FIXED_LOT_MANUAL


def test_demo_manual_lot_used_in_order(d_config: dict, store: ExecutionStore) -> None:
    fake = FakeMT5(_buy_rates(), trade_mode=FakeMT5.ACCOUNT_TRADE_MODE_DEMO)
    decision = robot.run_once(
        d_config,
        store,
        fake,
        bars=500,
        execution_enabled=True,
        confirm_demo_execution=True,
        execution_sizing_mode=robot.SIZING_MODE_FIXED_LOT_MANUAL,
        manual_lot=0.05,
        max_manual_risk_percent=100.0,  # no warning -> straightforward open
    )
    assert decision["intended_action"] == robot.ACTION_OPENED_BUY
    assert fake.sent[0]["volume"] == pytest.approx(0.05)
    assert decision["sizing"]["implied_risk_amount"] is not None


# ---------------------------------------------------------------------------
# API: requests accept the sizing fields and echo them back in the decision
# ---------------------------------------------------------------------------
@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def test_dry_run_endpoint_accepts_sizing_fields(
    client: TestClient,
    d_config: dict,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MT5_EXECUTION_ROBOT_DIR", str(tmp_path / "state"))
    fake = FakeMT5(_buy_rates(), trade_mode=FakeMT5.ACCOUNT_TRADE_MODE_DEMO)
    monkeypatch.setattr(manager, "_load_mt5", lambda: fake)
    resp = client.post(
        "/api/strategy-lab/execution/dry-run-once",
        json={
            "config": d_config,
            "execution_sizing_mode": "fixed_lot_manual",
            "manual_lot": 0.05,
        },
    )
    assert resp.status_code == 200
    sizing = resp.json()["sizing"]
    assert sizing["execution_sizing_mode"] == "fixed_lot_manual"
    assert sizing["manual_lot_requested"] == pytest.approx(0.05)
    assert fake.sent == []


def test_dry_run_endpoint_rejects_unknown_mode(
    client: TestClient, d_config: dict
) -> None:
    resp = client.post(
        "/api/strategy-lab/execution/dry-run-once",
        json={"config": d_config, "execution_sizing_mode": "martingale"},
    )
    assert resp.status_code == 422


def test_start_endpoint_rejects_negative_manual_lot(
    client: TestClient, d_config: dict
) -> None:
    resp = client.post(
        "/api/strategy-lab/execution/start",
        json={
            "config_path": "x.json",
            "execution_sizing_mode": "fixed_lot_manual",
            "manual_lot": -1,
        },
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# CLI: the new sizing arguments parse
# ---------------------------------------------------------------------------
def test_cli_parses_sizing_args() -> None:
    args = cli._build_parser().parse_args(
        [
            "--config",
            "x.json",
            "--once",
            "--execution-sizing-mode",
            "fixed_lot_manual",
            "--manual-lot",
            "0.05",
            "--max-manual-risk-percent",
            "2.5",
            "--allow-high-manual-risk",
        ]
    )
    assert args.execution_sizing_mode == "fixed_lot_manual"
    assert args.manual_lot == pytest.approx(0.05)
    assert args.max_manual_risk_percent == pytest.approx(2.5)
    assert args.allow_high_manual_risk is True


def test_cli_sizing_defaults() -> None:
    args = cli._build_parser().parse_args(["--config", "x.json", "--once"])
    assert args.execution_sizing_mode == robot.SIZING_MODE_RISK_PERCENT_AUTO
    assert args.manual_lot is None
    assert args.max_lot is None
    assert args.max_manual_risk_percent == pytest.approx(3.0)
    assert args.allow_high_manual_risk is False


def test_cli_rejects_unknown_sizing_mode() -> None:
    with pytest.raises(SystemExit):
        cli._build_parser().parse_args(
            ["--config", "x.json", "--execution-sizing-mode", "nope"]
        )
