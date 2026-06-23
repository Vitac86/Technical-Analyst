"""Strategy Lab v1.6 API: run, compare and export rule-based XAUUSD finalists.

These endpoints expose only the **production**, rule-based presets (D primary,
C secondary). The ML signal filter is research-only and is never enabled here.
All heavy lifting is delegated to :mod:`app.strategy_lab.lab_service`, which in
turn reuses the existing backtester / metrics modules.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.schemas.strategy_lab import (
    BacktestRequest,
    BacktestResponse,
    CompareRequest,
    CompareResponse,
    ExportConfigRequest,
    PresetOut,
    PresetsResponse,
)
from app.strategy_lab import lab_service, presets

router = APIRouter()


def _map_error(exc: Exception) -> HTTPException:
    """Translate service errors into clear HTTP responses."""
    if isinstance(exc, lab_service.LabError):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, lab_service.DataUnavailableError):
        return HTTPException(status_code=503, detail=str(exc))
    raise exc


# ---------------------------------------------------------------------------
# A. Presets
# ---------------------------------------------------------------------------
@router.get("/presets", response_model=PresetsResponse)
def get_presets() -> PresetsResponse:
    """List the confirmed rule-based presets (D primary, C secondary)."""
    preset_list = [
        PresetOut(
            preset_id=preset.preset_id,
            display_name=preset.display_name,
            description=preset.description,
            strategy_name=preset.strategy_name,
            family=preset.family,
            timeframe=preset.timeframe,
            direction_mode=preset.direction_mode,
            exit_mode=preset.exit_mode,
            sizing_mode=preset.sizing_mode,
            default_parameters=dict(preset.defaults),
            allowed_ranges=dict(preset.allowed_ranges),
            research_status=preset.research_status,
            recommended_use=preset.recommended_use,
            warning_notes=list(preset.warning_notes),
            is_default=preset.preset_id == presets.DEFAULT_PRESET_ID,
        )
        for preset in presets.PRESETS.values()
    ]
    return PresetsResponse(
        presets=preset_list,
        default_preset_id=presets.DEFAULT_PRESET_ID,
        cost_scenarios=lab_service.cost_scenarios_catalogue(),
        ml_filter_enabled=False,
        ml_note=presets.ML_RESEARCH_NOTE,
        disclaimer=presets.RESEARCH_DISCLAIMER,
    )


# ---------------------------------------------------------------------------
# B. Backtest
# ---------------------------------------------------------------------------
@router.post("/backtest", response_model=BacktestResponse)
def run_backtest(body: BacktestRequest) -> BacktestResponse:
    """Run one rule-based backtest and return metrics, curves and trades."""
    try:
        result = lab_service.run_backtest(
            preset_id=body.preset_id,
            symbol=body.symbol,
            timeframe=body.timeframe,
            overrides=body.overrides(),
            cost_scenario=body.cost_scenario,
            custom_costs=body.custom_costs_dict(),
            start=body.start_str(),
            end=body.end_str(),
            trades_limit=body.trades_limit,
            equity_points=body.equity_points,
        )
    except (lab_service.LabError, lab_service.DataUnavailableError) as exc:
        raise _map_error(exc) from exc
    return BacktestResponse.model_validate(result)


# ---------------------------------------------------------------------------
# C. Compare
# ---------------------------------------------------------------------------
@router.post("/compare", response_model=CompareResponse)
def compare(body: CompareRequest) -> CompareResponse:
    """Compare several strategy configs side by side on headline metrics."""
    configs = [
        {
            "label": cfg.label,
            "preset_id": cfg.preset_id,
            "symbol": cfg.symbol,
            "timeframe": cfg.timeframe,
            "overrides": cfg.overrides(),
            "cost_scenario": cfg.cost_scenario,
            "custom_costs": cfg.custom_costs_dict(),
            "start": cfg.start_str(),
            "end": cfg.end_str(),
        }
        for cfg in body.configs
    ]
    try:
        result = lab_service.compare_configs(configs)
    except (lab_service.LabError, lab_service.DataUnavailableError) as exc:
        raise _map_error(exc) from exc
    return CompareResponse.model_validate(result)


# ---------------------------------------------------------------------------
# D. Export config
# ---------------------------------------------------------------------------
@router.post("/export-config")
def export_config(body: ExportConfigRequest) -> JSONResponse:
    """Return a downloadable JSON config for a later MT5 robot / signal bridge."""
    try:
        config = lab_service.export_config(
            preset_id=body.preset_id,
            symbol=body.symbol,
            timeframe=body.timeframe,
            overrides=body.overrides(),
            cost_scenario=body.cost_scenario,
            custom_costs=body.custom_costs_dict(),
        )
    except (lab_service.LabError, lab_service.DataUnavailableError) as exc:
        raise _map_error(exc) from exc

    filename = f"{config['strategy_id']}_{config['symbol']}_{config['timeframe']}.json"
    return JSONResponse(
        content=config,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
