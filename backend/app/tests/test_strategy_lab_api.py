"""Tests for the Strategy Lab v1.6 rule-based API.

Backtest/compare tests need the read-only MT5 CSVs (which are git-ignored), so
they skip cleanly when the data is absent. The core pipeline is additionally
covered with injected synthetic data, and all validation / preset / export
behaviour runs without any market data at all.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.strategy_lab import lab_service, presets

client = TestClient(app)

D_PRESET = "D_supertrend_h4_trailing_risk"
C_PRESET = "C_donchian_h1_fixed_atr_risk"


def _has_data(symbol: str, timeframe: str) -> bool:
    try:
        lab_service._resolve_data_path(symbol, timeframe)
        return True
    except lab_service.DataUnavailableError:
        return False


requires_h4 = pytest.mark.skipif(
    not _has_data("XAUUSD", "H4"), reason="XAUUSD H4 market data not present"
)
requires_h1 = pytest.mark.skipif(
    not _has_data("XAUUSD", "H1"), reason="XAUUSD H1 market data not present"
)


def _synthetic_ohlc(n: int = 400) -> pd.DataFrame:
    """A down-then-up XAUUSD-like H4 series (forces a SuperTrend long flip)."""
    times = pd.date_range("2020-01-01", periods=n, freq="4h", tz="UTC")
    dip = np.linspace(2000.0, 1900.0, num=60)
    rally = np.linspace(1900.0, 2400.0, num=n - 60)
    close = np.concatenate([dip, rally])
    rng = np.sin(np.arange(n) / 3.0) * 4.0  # gentle intrabar wiggle
    high = close + np.abs(rng) + 5.0
    low = close - np.abs(rng) - 5.0
    open_ = close - rng * 0.5
    return pd.DataFrame(
        {
            "datetime": times,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "symbol": "XAUUSD",
            "timeframe": "H4",
        }
    )


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------
def test_presets_endpoint_returns_d_and_c() -> None:
    resp = client.get("/api/strategy-lab/presets")
    assert resp.status_code == 200
    body = resp.json()
    ids = {p["preset_id"] for p in body["presets"]}
    assert ids == {D_PRESET, C_PRESET}


def test_presets_default_is_d() -> None:
    body = client.get("/api/strategy-lab/presets").json()
    assert body["default_preset_id"] == D_PRESET
    default = [p for p in body["presets"] if p["is_default"]]
    assert len(default) == 1 and default[0]["preset_id"] == D_PRESET


def test_presets_expose_required_metadata() -> None:
    body = client.get("/api/strategy-lab/presets").json()
    for preset in body["presets"]:
        for key in (
            "display_name",
            "description",
            "default_parameters",
            "allowed_ranges",
            "research_status",
            "recommended_use",
            "warning_notes",
        ):
            assert key in preset and preset[key] not in (None, "", [])


def test_ml_filter_disabled_by_default_in_presets() -> None:
    body = client.get("/api/strategy-lab/presets").json()
    assert body["ml_filter_enabled"] is False
    assert "disabled by default" in body["ml_note"].lower()


# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------
@requires_h4
def test_backtest_d_default_config() -> None:
    resp = client.post("/api/strategy-lab/backtest", json={"preset_id": D_PRESET})
    assert resp.status_code == 200
    body = resp.json()
    assert body["preset_id"] == D_PRESET
    assert body["timeframe"] == "H4"
    assert body["summary"]["total_trades"] > 0
    assert len(body["equity_curve"]) > 0
    assert len(body["walk_forward_summary"]) > 0
    # Headline metric keys the UI cards rely on.
    for key in ("final_equity", "total_return_pct", "max_drawdown_pct", "win_rate"):
        assert key in body["summary"]


@requires_h1
def test_backtest_c_default_config() -> None:
    resp = client.post("/api/strategy-lab/backtest", json={"preset_id": C_PRESET})
    assert resp.status_code == 200
    body = resp.json()
    assert body["preset_id"] == C_PRESET
    assert body["timeframe"] == "H1"
    assert body["summary"]["total_trades"] > 0


@requires_h4
def test_backtest_respects_trades_limit() -> None:
    resp = client.post(
        "/api/strategy-lab/backtest",
        json={"preset_id": D_PRESET, "trades_limit": 5},
    )
    body = resp.json()
    assert len(body["trades"]) <= 5
    if body["trades_total"] > 5:
        assert body["trades_truncated"] is True


def test_backtest_with_injected_synthetic_data() -> None:
    """The full run pipeline works without touching the read-only CSVs."""
    result = lab_service.run_backtest(preset_id=D_PRESET, df=_synthetic_ohlc())
    assert result["summary"]["total_trades"] >= 1
    assert result["trades"], "expected at least one executed trade"
    assert result["summary"]["stop_out_count"] == 0


def test_backtest_invalid_parameter_returns_clear_error() -> None:
    resp = client.post(
        "/api/strategy-lab/backtest",
        json={"preset_id": D_PRESET, "multiplier": 99.0},
    )
    assert resp.status_code == 422
    assert "multiplier" in resp.json()["detail"]


def test_backtest_unknown_preset_returns_error() -> None:
    resp = client.post("/api/strategy-lab/backtest", json={"preset_id": "does_not_exist"})
    assert resp.status_code == 422
    assert "Unknown preset_id" in resp.json()["detail"]


def test_backtest_custom_cost_requires_costs() -> None:
    resp = client.post(
        "/api/strategy-lab/backtest",
        json={"preset_id": D_PRESET, "cost_scenario": "Custom"},
    )
    assert resp.status_code == 422
    assert "Custom" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------
@requires_h4
@requires_h1
def test_compare_d_and_c() -> None:
    resp = client.post(
        "/api/strategy-lab/compare",
        json={
            "configs": [
                {"preset_id": D_PRESET, "label": "D base"},
                {"preset_id": C_PRESET, "label": "C base"},
            ]
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert [r["label"] for r in body["rows"]] == ["D base", "C base"]
    for required in (
        "final_equity",
        "total_return_pct",
        "profit_factor",
        "max_drawdown_pct",
        "max_effective_leverage",
        "stop_out_count",
    ):
        assert required in body["fields"]


# ---------------------------------------------------------------------------
# Export config
# ---------------------------------------------------------------------------
def test_export_config_returns_valid_json() -> None:
    resp = client.post(
        "/api/strategy-lab/export-config",
        json={"preset_id": D_PRESET, "multiplier": 2.5},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert "attachment" in resp.headers.get("content-disposition", "")
    config = resp.json()
    for key in (
        "strategy_id",
        "symbol",
        "timeframe",
        "strategy_name",
        "direction_mode",
        "strategy_parameters",
        "exit_parameters",
        "risk_parameters",
        "cost_assumptions",
        "version",
        "created_at",
        "notes",
    ):
        assert key in config
    assert config["strategy_parameters"]["multiplier"] == 2.5
    assert config["version"] == lab_service.CONFIG_VERSION


def test_export_config_marks_ml_filter_disabled() -> None:
    config = client.post(
        "/api/strategy-lab/export-config", json={"preset_id": D_PRESET}
    ).json()
    assert config["ml_filter_enabled"] is False


def test_export_config_unknown_preset_returns_error() -> None:
    resp = client.post("/api/strategy-lab/export-config", json={"preset_id": "nope"})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# ML stays research-only / disabled
# ---------------------------------------------------------------------------
def test_ml_not_enabled_anywhere_by_default() -> None:
    presets_body = client.get("/api/strategy-lab/presets").json()
    assert presets_body["ml_filter_enabled"] is False
    # No preset advertises ML as part of its production configuration.
    for preset in presets.PRESETS.values():
        assert "ml" not in preset.family.lower()
        assert preset.sizing_mode in ("risk_percent", "fixed_lot")
