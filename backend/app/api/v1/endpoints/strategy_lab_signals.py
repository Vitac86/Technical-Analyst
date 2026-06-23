"""Strategy Lab v1.7 / v1.7.1: signal-only bridge endpoints (no execution).

v1.7 added read-only endpoints (``/latest`` / ``/history``) that read the files
written by the independent ``run_mt5_signal_bridge.py`` process.

v1.7.1 adds a UI control layer on top of the **same** signal-only bridge:
save/list configs, check MT5 readiness, run one check, start/stop polling, and
read status/logs. All control flows go through
:mod:`app.strategy_lab.mt5_bridge_manager`, which reuses the bridge core.

There are intentionally **no** order-execution endpoints, and every response
carries ``execution_enabled: false``. The bridge is signal-only.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.schemas.strategy_lab import (
    CheckOnceRequest,
    Mt5CheckRequest,
    SaveSignalConfigRequest,
    StartBridgeRequest,
)
from app.strategy_lab import mt5_bridge_manager as manager
from app.strategy_lab import mt5_signal_bridge as bridge
from app.strategy_lab.signal_store import SignalStore

router = APIRouter()


def _store() -> SignalStore:
    """Open the signal store at the configured/default output directory."""
    return SignalStore()


def _bridge_error(exc: bridge.BridgeError) -> HTTPException:
    """A bad config / bad path is a client error (422)."""
    return HTTPException(status_code=422, detail=str(exc))


# ---------------------------------------------------------------------------
# Read-only signal output (v1.7)
# ---------------------------------------------------------------------------
@router.get("/latest")
def get_latest_signal() -> dict:
    """Return the most recently emitted signal (or ``null`` if none yet)."""
    return {"signal": _store().read_latest(), "execution_enabled": False}


@router.get("/history")
def get_signal_history(limit: int = Query(default=50, ge=1, le=1000)) -> dict:
    """Return up to ``limit`` most-recent signals (newest first)."""
    signals = _store().read_history(limit=limit)
    return {"signals": signals, "count": len(signals), "execution_enabled": False}


# ---------------------------------------------------------------------------
# A. Save a config for the bridge
# ---------------------------------------------------------------------------
@router.post("/configs/save")
def save_signal_config(body: SaveSignalConfigRequest) -> dict:
    """Validate and save a strategy config JSON for the signal-only bridge."""
    try:
        saved = manager.save_config(body.config, body.name)
    except bridge.BridgeError as exc:
        raise _bridge_error(exc) from exc
    return {**saved, "execution_enabled": False}


# ---------------------------------------------------------------------------
# B. List saved configs
# ---------------------------------------------------------------------------
@router.get("/configs")
def list_signal_configs() -> dict:
    """List the saved configs available to the bridge."""
    return {"configs": manager.list_configs(), "execution_enabled": False}


# ---------------------------------------------------------------------------
# C. MT5 readiness (no signal emission, no trade calls)
# ---------------------------------------------------------------------------
@router.post("/mt5-check")
def mt5_check(body: Mt5CheckRequest) -> dict:
    """Check MT5 package / terminal / symbol / rates readiness for a config."""
    try:
        config = manager.resolve_input_config(body.config, body.config_path)
    except bridge.BridgeError as exc:
        raise _bridge_error(exc) from exc
    return manager.check_mt5_readiness(config, bars=body.bars)


# ---------------------------------------------------------------------------
# D. Run one signal-only check
# ---------------------------------------------------------------------------
@router.post("/check-once")
def check_once(body: CheckOnceRequest) -> dict:
    """Run a single signal-only check, persisting it through the signal store."""
    try:
        config = manager.resolve_input_config(body.config, body.config_path)
    except bridge.BridgeError as exc:
        raise _bridge_error(exc) from exc
    try:
        result = manager.run_check_once(config, bars=body.bars)
    except bridge.BridgeError as exc:
        # MT5 operational issue (package/terminal/symbol/rates): actionable, not
        # a malformed request -- surface it inline so the UI can guide the user.
        return {
            "ok": False,
            "emitted": False,
            "signal": manager.latest_signal(),
            "stdout": "",
            "stderr": str(exc),
            "execution_enabled": False,
        }
    return {"ok": True, **result}


# ---------------------------------------------------------------------------
# E. Start polling
# ---------------------------------------------------------------------------
@router.post("/start")
def start_signal_bridge(body: StartBridgeRequest) -> dict:
    """Start the signal-only polling bridge as a managed subprocess."""
    try:
        return manager.start_polling(
            body.config_path, poll_seconds=body.poll_seconds, bars=body.bars
        )
    except bridge.BridgeError as exc:
        raise _bridge_error(exc) from exc


# ---------------------------------------------------------------------------
# F. Stop polling
# ---------------------------------------------------------------------------
@router.post("/stop")
def stop_signal_bridge() -> dict:
    """Stop the managed polling bridge process."""
    return manager.stop_polling()


# ---------------------------------------------------------------------------
# G. Status
# ---------------------------------------------------------------------------
@router.get("/status")
def signal_bridge_status() -> dict:
    """Return bridge process status, the latest signal, and log excerpts."""
    return manager.bridge_status()


# ---------------------------------------------------------------------------
# H. Logs
# ---------------------------------------------------------------------------
@router.get("/logs")
def signal_bridge_logs(lines: int = Query(default=100, ge=1, le=5000)) -> dict:
    """Return the last ``lines`` of the bridge stdout/stderr logs."""
    return {**manager.tail_logs(lines), "execution_enabled": False}
