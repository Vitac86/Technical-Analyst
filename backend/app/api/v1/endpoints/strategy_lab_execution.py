"""Strategy Lab v1.8: MT5 **demo execution robot** endpoints.

These endpoints are a **separate** API surface from the signal-only bridge
(``/api/strategy-lab/signals``). The signal-only bridge is never converted into a
trading component: this router talks only to
:mod:`app.strategy_lab.mt5_execution_manager`, which owns the demo safety gates.

Hard safety guarantees surfaced here:

    * dry-run is the default and never sends orders;
    * demo execution is refused unless ``confirm_demo_execution`` is true **and**
      the connected MT5 account is detected as a demo account;
    * live execution is not implemented and is always refused.

Every response is decision-shaped (or a process-status), so the UI can render a
refusal in the same card as a real decision. There is no "go live" switch.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.schemas.strategy_lab import (
    ExecutionDemoOnceRequest,
    ExecutionDryRunRequest,
    SaveExecutionConfigRequest,
    StartExecutionRequest,
)
from app.strategy_lab import mt5_execution_manager as manager
from app.strategy_lab import mt5_execution_robot as robot
from app.strategy_lab import mt5_signal_bridge as bridge

router = APIRouter()


def _client_error(message: str) -> HTTPException:
    """A bad config / bad path is a client error (422)."""
    return HTTPException(status_code=422, detail=message)


def _sizing_kwargs(body) -> dict:  # type: ignore[no-untyped-def]
    """Extract the v1.9 position-sizing controls from a request body."""
    return {
        "execution_sizing_mode": body.execution_sizing_mode,
        "manual_lot": body.manual_lot,
        "max_lot": body.max_lot,
        "max_manual_risk_percent": body.max_manual_risk_percent,
        "allow_high_manual_risk": body.allow_high_manual_risk,
    }


def _resolve_or_refuse(
    config: dict | None, config_path: str | None
) -> tuple[dict | None, dict | None]:
    """Resolve a config, or return a decision-shaped refusal for unsupported ones.

    Returns ``(config, None)`` on success or ``(None, refusal_decision)`` when the
    config is unsupported (e.g. finalist C), so the caller can return a clean
    refusal card instead of an HTTP error.
    """
    try:
        resolved = manager.resolve_input_config(config, config_path)
        return resolved, None
    except robot.ExecutionError as exc:
        # Unsupported config (C / ML / non-long-only): refuse with a clear message.
        base = config or {}
        refusal = robot.manual_refusal_decision(base, reasons=[str(exc)])
        return None, refusal
    except bridge.BridgeError as exc:
        raise _client_error(str(exc)) from exc


# ---------------------------------------------------------------------------
# Config save / list (shared configs dir; robot supports only D)
# ---------------------------------------------------------------------------
@router.post("/configs/save")
def save_execution_config(body: SaveExecutionConfigRequest) -> dict:
    """Validate (D-only) and save a config for the demo execution robot."""
    try:
        saved = manager.save_config(body.config, body.name)
    except robot.ExecutionError as exc:
        raise _client_error(str(exc)) from exc
    except bridge.BridgeError as exc:
        raise _client_error(str(exc)) from exc
    return {**saved, "execution_robot_version": robot.EXECUTION_ROBOT_VERSION}


@router.get("/configs")
def list_execution_configs() -> dict:
    """List saved configs, flagged with whether the robot supports each one."""
    return {
        "configs": manager.list_configs(),
        "supported_strategy_id": robot.SUPPORTED_STRATEGY_ID,
        "execution_robot_version": robot.EXECUTION_ROBOT_VERSION,
    }


# ---------------------------------------------------------------------------
# A. Dry-run once (never sends orders)
# ---------------------------------------------------------------------------
@router.post("/dry-run-once")
def dry_run_once(body: ExecutionDryRunRequest) -> dict:
    """Run a single dry-run decision. Safe on any account; never executes."""
    config, refusal = _resolve_or_refuse(body.config, body.config_path)
    if refusal is not None:
        return refusal
    try:
        return manager.run_dry_run_once(
            config,
            bars=body.bars,
            magic=body.magic,
            deviation=body.deviation,
            allow_min_lot_rounding=body.allow_min_lot_rounding,
            **_sizing_kwargs(body),
        )
    except (bridge.BridgeError, robot.ExecutionError) as exc:
        # MT5 operational issue: surface as a refusal card, not an HTTP error.
        return robot.manual_refusal_decision(config, reasons=[str(exc)])


# ---------------------------------------------------------------------------
# B. Demo execution once (refused without confirmation / on non-demo accounts)
# ---------------------------------------------------------------------------
@router.post("/demo-once")
def demo_execution_once(body: ExecutionDemoOnceRequest) -> dict:
    """Run one demo-execution decision. Refused unless explicitly confirmed."""
    config, refusal = _resolve_or_refuse(body.config, body.config_path)
    if refusal is not None:
        return refusal
    try:
        return manager.run_demo_once(
            config,
            confirm_demo_execution=body.confirm_demo_execution,
            bars=body.bars,
            magic=body.magic,
            deviation=body.deviation,
            allow_min_lot_rounding=body.allow_min_lot_rounding,
            **_sizing_kwargs(body),
        )
    except (bridge.BridgeError, robot.ExecutionError) as exc:
        return robot.manual_refusal_decision(
            config, reasons=[str(exc)], execution_enabled=True
        )


# ---------------------------------------------------------------------------
# C. Start polling robot (dry-run unless demo confirmed)
# ---------------------------------------------------------------------------
@router.post("/start")
def start_execution_robot(body: StartExecutionRequest) -> dict:
    """Start the polling robot. Demo execution requires explicit confirmation."""
    try:
        return manager.start_polling(
            body.config_path,
            poll_seconds=body.poll_seconds,
            bars=body.bars,
            magic=body.magic,
            deviation=body.deviation,
            demo_execution_enabled=body.demo_execution_enabled,
            confirm_demo_execution=body.confirm_demo_execution,
            allow_min_lot_rounding=body.allow_min_lot_rounding,
            **_sizing_kwargs(body),
        )
    except robot.ExecutionError as exc:
        raise _client_error(str(exc)) from exc
    except bridge.BridgeError as exc:
        raise _client_error(str(exc)) from exc


# ---------------------------------------------------------------------------
# D. Stop polling robot
# ---------------------------------------------------------------------------
@router.post("/stop")
def stop_execution_robot() -> dict:
    """Stop the managed polling robot process."""
    return manager.stop_polling()


# ---------------------------------------------------------------------------
# E. Status
# ---------------------------------------------------------------------------
@router.get("/status")
def execution_robot_status() -> dict:
    """Return process status + the latest decision + log excerpts."""
    return manager.robot_status()


# ---------------------------------------------------------------------------
# F. Latest decision
# ---------------------------------------------------------------------------
@router.get("/latest")
def latest_execution_decision() -> dict:
    """Return the latest decision record (or ``null``)."""
    return {
        "latest_execution_decision": manager.latest_decision(),
        "execution_robot_version": robot.EXECUTION_ROBOT_VERSION,
    }


# ---------------------------------------------------------------------------
# G. History
# ---------------------------------------------------------------------------
@router.get("/history")
def execution_history(limit: int = Query(default=50, ge=1, le=1000)) -> dict:
    """Return up to ``limit`` recent decisions (newest first)."""
    rows = manager.execution_history(limit=limit)
    return {
        "events": rows,
        "count": len(rows),
        "execution_robot_version": robot.EXECUTION_ROBOT_VERSION,
    }


# ---------------------------------------------------------------------------
# H. Logs
# ---------------------------------------------------------------------------
@router.get("/logs")
def execution_robot_logs(lines: int = Query(default=100, ge=1, le=5000)) -> dict:
    """Return the last ``lines`` of the robot stdout/stderr logs."""
    return {
        **manager.tail_logs(lines),
        "execution_robot_version": robot.EXECUTION_ROBOT_VERSION,
    }
