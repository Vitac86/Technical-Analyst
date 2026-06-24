"""Strategy Lab v1.8: management layer for the **demo execution robot**.

This module is the glue between the Strategy Lab UI/API and the demo execution
robot (:mod:`app.strategy_lab.mt5_execution_robot`). It is the execution-side
counterpart of :mod:`app.strategy_lab.mt5_bridge_manager` (which manages the
signal-only bridge) and is kept **fully separate** -- a separate process state
file, separate logs and a separate output directory -- so the signal-only bridge
is never affected.

Responsibilities (all routed through the robot, which owns the safety gates):

    * run one dry-run check (never sends orders);
    * run one demo-execution check (refuses without confirmation / on a non-demo
      account, otherwise lets the robot's gates decide);
    * start / stop a polling subprocess (the CLI runner) and report its status;
    * read the latest decision and the execution history;
    * tail the robot's stdout/stderr logs.

Saved configs are **shared** with the signal bridge (same
``MetaTrader_Data/configs/`` directory): the robot simply refuses any config
that is not the supported D strategy. Process/PID handling reuses the
signal-bridge manager's Windows-aware helpers to avoid duplication.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

try:  # package import
    from . import mt5_bridge_manager as bridge_manager
    from . import mt5_execution_robot as robot
    from . import mt5_signal_bridge as bridge
    from .execution_store import (
        ExecutionStore,
        STDERR_LOG_FILENAME,
        STDOUT_LOG_FILENAME,
        default_output_dir,
    )
except ImportError:  # pragma: no cover - script import fallback
    import mt5_bridge_manager as bridge_manager  # type: ignore[no-redef]
    import mt5_execution_robot as robot  # type: ignore[no-redef]
    import mt5_signal_bridge as bridge  # type: ignore[no-redef]
    from execution_store import (  # type: ignore[no-redef]
        ExecutionStore,
        STDERR_LOG_FILENAME,
        STDOUT_LOG_FILENAME,
        default_output_dir,
    )

# Repo root: mt5_execution_manager.py -> strategy_lab -> app -> backend -> ROOT
REPO_ROOT = Path(__file__).resolve().parents[3]
RUN_SCRIPT = Path(__file__).resolve().parent / "run_mt5_execution_robot.py"

PROCESS_STATE_FILENAME = "execution_process.json"

# Type for a function that returns the MetaTrader5 module (injected in tests).
Mt5Loader = Callable[[], object]


# ---------------------------------------------------------------------------
# Directories (shared configs dir with the signal bridge; separate state dir)
# ---------------------------------------------------------------------------
def state_dir() -> Path:
    """Resolve the execution-robot state/log directory (env-overridable)."""
    path = default_output_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _store() -> ExecutionStore:
    return ExecutionStore()


# ---------------------------------------------------------------------------
# Config save / list / resolution (configs shared with the signal bridge)
# ---------------------------------------------------------------------------
def save_config(config: dict, name: Optional[str] = None) -> dict:
    """Validate (D-only) and save a config into the shared configs directory."""
    robot.validate_execution_config(config)
    saved = bridge_manager.save_config(config, name)
    saved["config_summary"] = robot.config_summary(config)
    return saved


def list_configs() -> list[dict]:
    """List saved configs, annotated with whether the robot supports each one."""
    entries = bridge_manager.list_configs()
    for entry in entries:
        supported = entry.get("strategy_id") == robot.SUPPORTED_STRATEGY_ID
        entry["is_supported"] = supported
        entry["unsupported_reason"] = (
            None if supported else robot.UNSUPPORTED_CONFIG_MESSAGE
        )
    return entries


def resolve_input_config(
    config: Optional[dict] = None, config_path: Optional[str] = None
) -> dict:
    """Return a config validated for execution (D-only) from an object or path."""
    if config is not None:
        robot.validate_execution_config(config)
        return config
    if config_path:
        loaded = bridge.load_config(bridge_manager.resolve_config_path(config_path))
        robot.validate_execution_config(loaded)
        return loaded
    raise robot.ExecutionError("Provide either 'config_path' or 'config'.")


# ---------------------------------------------------------------------------
# MT5 access (thin wrapper so tests can inject a fake module)
# ---------------------------------------------------------------------------
def _load_mt5():  # type: ignore[no-untyped-def]
    """Load the MetaTrader5 module via the bridge (clear hint if absent)."""
    return bridge.load_mt5()


def _run_once_with_mt5(
    config: dict,
    *,
    execution_enabled: bool,
    confirm_demo_execution: bool,
    bars: int,
    magic: int,
    deviation: int,
    allow_min_lot_rounding: bool,
    execution_sizing_mode: str = robot.DEFAULT_EXECUTION_SIZING_MODE,
    manual_lot: Optional[float] = None,
    max_lot: Optional[float] = None,
    max_manual_risk_percent: float = robot.DEFAULT_MAX_MANUAL_RISK_PERCENT,
    allow_high_manual_risk: bool = False,
    mt5_loader: Optional[Mt5Loader],
    store: Optional[ExecutionStore],
) -> dict:
    """Connect to MT5, run one robot decision and shut the connection down."""
    loader = mt5_loader or _load_mt5
    store = store or _store()
    mt5 = loader()
    bridge.initialize_mt5(mt5)
    try:
        return robot.run_once(
            config,
            store,
            mt5,
            bars=bars,
            execution_enabled=execution_enabled,
            confirm_demo_execution=confirm_demo_execution,
            magic=magic,
            deviation=deviation,
            allow_min_lot_rounding=allow_min_lot_rounding,
            execution_sizing_mode=execution_sizing_mode,
            manual_lot=manual_lot,
            max_lot=max_lot,
            max_manual_risk_percent=max_manual_risk_percent,
            allow_high_manual_risk=allow_high_manual_risk,
            generated_at=datetime.now(timezone.utc),
        )
    finally:
        bridge.shutdown_mt5(mt5)


def run_dry_run_once(
    config: dict,
    *,
    bars: int = bridge.DEFAULT_BARS,
    magic: int = robot.DEFAULT_MAGIC,
    deviation: int = robot.DEFAULT_DEVIATION,
    allow_min_lot_rounding: bool = False,
    execution_sizing_mode: str = robot.DEFAULT_EXECUTION_SIZING_MODE,
    manual_lot: Optional[float] = None,
    max_lot: Optional[float] = None,
    max_manual_risk_percent: float = robot.DEFAULT_MAX_MANUAL_RISK_PERCENT,
    allow_high_manual_risk: bool = False,
    mt5_loader: Optional[Mt5Loader] = None,
    store: Optional[ExecutionStore] = None,
) -> dict:
    """Run one **dry-run** decision. Never sends an order on any account."""
    robot.validate_execution_config(config)
    return _run_once_with_mt5(
        config,
        execution_enabled=False,
        confirm_demo_execution=False,
        bars=bars,
        magic=magic,
        deviation=deviation,
        allow_min_lot_rounding=allow_min_lot_rounding,
        execution_sizing_mode=execution_sizing_mode,
        manual_lot=manual_lot,
        max_lot=max_lot,
        max_manual_risk_percent=max_manual_risk_percent,
        allow_high_manual_risk=allow_high_manual_risk,
        mt5_loader=mt5_loader,
        store=store,
    )


def run_demo_once(
    config: dict,
    *,
    confirm_demo_execution: bool,
    bars: int = bridge.DEFAULT_BARS,
    magic: int = robot.DEFAULT_MAGIC,
    deviation: int = robot.DEFAULT_DEVIATION,
    allow_min_lot_rounding: bool = False,
    execution_sizing_mode: str = robot.DEFAULT_EXECUTION_SIZING_MODE,
    manual_lot: Optional[float] = None,
    max_lot: Optional[float] = None,
    max_manual_risk_percent: float = robot.DEFAULT_MAX_MANUAL_RISK_PERCENT,
    allow_high_manual_risk: bool = False,
    mt5_loader: Optional[Mt5Loader] = None,
    store: Optional[ExecutionStore] = None,
) -> dict:
    """Run one **demo-execution** decision.

    Refuses *before contacting MT5* when ``confirm_demo_execution`` is not true.
    When confirmed, the robot still refuses a live/unknown account and any
    unsupported / unsizable setup -- the gates live in one place (the robot).
    """
    robot.validate_execution_config(config)
    store = store or _store()
    if not confirm_demo_execution:
        decision = robot.manual_refusal_decision(
            config,
            reasons=[robot.REFUSE_NOT_CONFIRMED],
            execution_enabled=True,
            demo_only=True,
        )
        store.write_decision(decision)
        return decision

    return _run_once_with_mt5(
        config,
        execution_enabled=True,
        confirm_demo_execution=True,
        bars=bars,
        magic=magic,
        deviation=deviation,
        allow_min_lot_rounding=allow_min_lot_rounding,
        execution_sizing_mode=execution_sizing_mode,
        manual_lot=manual_lot,
        max_lot=max_lot,
        max_manual_risk_percent=max_manual_risk_percent,
        allow_high_manual_risk=allow_high_manual_risk,
        mt5_loader=mt5_loader,
        store=store,
    )


# ---------------------------------------------------------------------------
# Latest decision / history reads
# ---------------------------------------------------------------------------
def latest_decision() -> Optional[dict]:
    """Read the latest decision record (or ``None``)."""
    return _store().read_latest()


def execution_history(limit: int = 50) -> list[dict]:
    """Read up to ``limit`` recent decisions from the event log (newest first)."""
    return _store().read_history(limit=limit)


# ---------------------------------------------------------------------------
# Process management (start / stop / status) -- reuses the bridge manager's
# Windows-aware PID helpers; state + logs live in the execution directory.
# ---------------------------------------------------------------------------
def _process_state_path() -> Path:
    return state_dir() / PROCESS_STATE_FILENAME


def read_process_state() -> dict:
    """Load the persisted robot-process state (or an empty dict)."""
    path = _process_state_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_process_state(state: dict) -> None:
    _process_state_path().write_text(json.dumps(state, indent=2), encoding="utf-8")


def process_status() -> dict:
    """Report whether the polling robot is running, reconciling stale state."""
    state = read_process_state()
    pid = state.get("pid")
    running = bool(pid) and bridge_manager._pid_alive(int(pid))

    if state and not running and state.get("status") == "running":
        state["status"] = "stopped"
        state["stopped_at"] = _now_iso()
        state["stopped_reason"] = "process_exited"
        _write_process_state(state)

    return {
        "running": running,
        "pid": pid,
        "started_at": state.get("started_at"),
        "mode": state.get("mode"),
        "config_path": state.get("config_path"),
        "poll_seconds": state.get("poll_seconds"),
        "bars": state.get("bars"),
        "status": "running" if running else state.get("status", "stopped"),
        "execution_robot_version": robot.EXECUTION_ROBOT_VERSION,
    }


def start_polling(
    config_path: str,
    *,
    poll_seconds: int = 60,
    bars: int = bridge.DEFAULT_BARS,
    magic: int = robot.DEFAULT_MAGIC,
    deviation: int = robot.DEFAULT_DEVIATION,
    demo_execution_enabled: bool = False,
    confirm_demo_execution: bool = False,
    allow_min_lot_rounding: bool = False,
    execution_sizing_mode: str = robot.DEFAULT_EXECUTION_SIZING_MODE,
    manual_lot: Optional[float] = None,
    max_lot: Optional[float] = None,
    max_manual_risk_percent: float = robot.DEFAULT_MAX_MANUAL_RISK_PERCENT,
    allow_high_manual_risk: bool = False,
) -> dict:
    """Start the polling robot subprocess, refusing a duplicate or unconfirmed run.

    Polling runs in dry-run mode unless ``demo_execution_enabled`` **and**
    ``confirm_demo_execution`` are both true. A demo-execution request without
    confirmation is refused here (no process is started). The position sizing
    mode (and its manual_lot / max_lot / manual-risk knobs) is forwarded to the
    polling subprocess so it sizes exactly like the one-shot path.
    """
    if execution_sizing_mode not in robot.SIZING_MODES:
        return {
            **process_status(),
            "started": False,
            "message": f"Unknown execution_sizing_mode '{execution_sizing_mode}'.",
        }
    if (
        execution_sizing_mode == robot.SIZING_MODE_FIXED_LOT_MANUAL
        and (manual_lot is None or manual_lot <= 0)
    ):
        return {
            **process_status(),
            "started": False,
            "message": "fixed_lot_manual requires a positive manual_lot.",
        }

    if demo_execution_enabled and not confirm_demo_execution:
        return {
            **process_status(),
            "started": False,
            "message": "Demo execution polling requires confirm_demo_execution=true.",
        }

    current = process_status()
    if current["running"]:
        return {**current, "started": False, "message": "Execution robot already running."}

    resolved = bridge_manager.resolve_config_path(config_path)
    robot.validate_execution_config(bridge.load_config(resolved))

    directory = state_dir()
    stdout_path = directory / STDOUT_LOG_FILENAME
    stderr_path = directory / STDERR_LOG_FILENAME

    mode = (
        robot.MODE_DEMO_EXECUTION
        if (demo_execution_enabled and confirm_demo_execution)
        else robot.MODE_DRY_RUN
    )

    command = [
        sys.executable,
        str(RUN_SCRIPT),
        "--config",
        str(resolved),
        "--poll-seconds",
        str(int(poll_seconds)),
        "--bars",
        str(int(bars)),
        "--magic",
        str(int(magic)),
        "--deviation",
        str(int(deviation)),
        "--output-dir",
        str(directory),
    ]
    if allow_min_lot_rounding:
        command.append("--allow-min-lot-rounding")
    command += [
        "--execution-sizing-mode",
        str(execution_sizing_mode),
        "--max-manual-risk-percent",
        str(float(max_manual_risk_percent)),
    ]
    if manual_lot is not None:
        command += ["--manual-lot", str(float(manual_lot))]
    if max_lot is not None:
        command += ["--max-lot", str(float(max_lot))]
    if allow_high_manual_risk:
        command.append("--allow-high-manual-risk")
    if mode == robot.MODE_DEMO_EXECUTION:
        command += ["--execution-enabled", "--confirm-demo-execution"]

    with stdout_path.open("w", encoding="utf-8") as out, stderr_path.open(
        "w", encoding="utf-8"
    ) as err:
        process = subprocess.Popen(  # noqa: S603 - fixed args, no shell
            command,
            stdout=out,
            stderr=err,
            cwd=str(REPO_ROOT),
            **bridge_manager._popen_kwargs(),
        )

    state = {
        "pid": process.pid,
        "started_at": _now_iso(),
        "mode": mode,
        "config_path": str(resolved),
        "poll_seconds": int(poll_seconds),
        "bars": int(bars),
        "magic": int(magic),
        "deviation": int(deviation),
        "allow_min_lot_rounding": bool(allow_min_lot_rounding),
        "execution_sizing_mode": str(execution_sizing_mode),
        "manual_lot": float(manual_lot) if manual_lot is not None else None,
        "max_lot": float(max_lot) if max_lot is not None else None,
        "max_manual_risk_percent": float(max_manual_risk_percent),
        "allow_high_manual_risk": bool(allow_high_manual_risk),
        "status": "running",
    }
    _write_process_state(state)
    return {
        **process_status(),
        "started": True,
        "message": f"Execution robot polling started ({mode}).",
    }


def stop_polling() -> dict:
    """Stop the managed polling robot process and update the state file."""
    state = read_process_state()
    pid = state.get("pid")

    if not pid or not bridge_manager._pid_alive(int(pid)):
        if state:
            state["status"] = "stopped"
            state.setdefault("stopped_at", _now_iso())
            _write_process_state(state)
        return {
            **process_status(),
            "stopped": True,
            "message": "Execution robot not running.",
        }

    bridge_manager._stop_pid(int(pid))
    state["status"] = "stopped"
    state["stopped_at"] = _now_iso()
    _write_process_state(state)
    return {**process_status(), "stopped": True, "message": "Execution robot stopped."}


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------
def tail_logs(lines: int = 100) -> dict:
    """Return the last ``lines`` of the robot stdout/stderr logs."""
    directory = state_dir()
    return {
        "stdout_tail": bridge_manager._tail_file(
            directory / STDOUT_LOG_FILENAME, lines
        ),
        "stderr_tail": bridge_manager._tail_file(
            directory / STDERR_LOG_FILENAME, lines
        ),
    }


def robot_status(log_excerpt_lines: int = 20) -> dict:
    """Rich status for the UI: process status + latest decision + log excerpts."""
    status = process_status()
    latest = latest_decision()
    logs = tail_logs(log_excerpt_lines)
    status.update(
        {
            "latest_execution_decision": latest,
            "latest_log_excerpt": logs["stdout_tail"],
            "error_excerpt": logs["stderr_tail"] or None,
        }
    )
    return status
