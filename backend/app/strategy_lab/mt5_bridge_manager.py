"""Strategy Lab v1.7.1: management layer for the **signal-only** MT5 bridge.

This module is the thin glue between the Strategy Lab UI/API and the existing
signal-only bridge. It does NOT contain any indicator, strategy or backtest
logic -- it reuses :mod:`app.strategy_lab.mt5_signal_bridge` (the bridge core)
and :mod:`app.strategy_lab.signal_store` (file persistence) for everything.

    v1.7.1 stays signal-only. Execution is intentionally disabled.

Responsibilities (all signal-only -- nothing here can ever place a trade):

    * save a Strategy Lab config JSON into ``MetaTrader_Data/configs/``;
    * list the saved configs with a small summary;
    * check MT5 readiness (package / terminal / account / symbol / rates) with no
      signal emission and no trade calls;
    * run one signal check by calling the bridge core directly;
    * start / stop a polling subprocess (the existing CLI runner) and report its
      status, all keyed off a local state file + OS PID checks (Windows-aware);
    * tail the bridge log files.

The polling subprocess is launched with the same Python interpreter as the
backend (``sys.executable``) and writes stdout/stderr to log files. The only
state kept between requests lives in
``MetaTrader_Data/reports/mt5_signal_bridge/bridge_process.json`` plus those log
files, so the status survives a backend restart and never relies on an
in-memory process handle.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

try:  # package import
    from . import mt5_signal_bridge as bridge
    from .signal_store import SignalStore, default_output_dir
except ImportError:  # pragma: no cover - script import fallback
    import mt5_signal_bridge as bridge  # type: ignore[no-redef]
    from signal_store import SignalStore, default_output_dir  # type: ignore[no-redef]


# Repo root: mt5_bridge_manager.py -> strategy_lab -> app -> backend -> ROOT
REPO_ROOT = Path(__file__).resolve().parents[3]
RUN_SCRIPT = Path(__file__).resolve().parent / "run_mt5_signal_bridge.py"

# Saved configs live here; env override keeps the API and tests isolated.
_ENV_CONFIGS_DIR = "MT5_SIGNAL_BRIDGE_CONFIGS_DIR"
DEFAULT_CONFIGS_DIR = REPO_ROOT / "MetaTrader_Data" / "configs"

PROCESS_STATE_FILENAME = "bridge_process.json"
STDOUT_LOG_FILENAME = "bridge_stdout.log"
STDERR_LOG_FILENAME = "bridge_stderr.log"

# Minimum closed bars for the readiness check to report "ok" (indicators need
# enough history); fewer is a warning, not an error.
READINESS_MIN_BARS = 60

# Type for a function that returns the MetaTrader5 module (injected in tests).
Mt5Loader = Callable[[], object]


# ---------------------------------------------------------------------------
# Directories (env-overridable for isolation)
# ---------------------------------------------------------------------------
def configs_dir() -> Path:
    """Resolve the saved-config directory (env override wins) and ensure it exists."""
    override = os.environ.get(_ENV_CONFIGS_DIR)
    path = Path(override) if override else DEFAULT_CONFIGS_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_dir() -> Path:
    """Resolve the bridge state/log directory (shared with the signal store)."""
    path = default_output_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Config save / list
# ---------------------------------------------------------------------------
def _safe_config_name(name: str) -> str:
    """Sanitise a user-supplied name into a safe ``<slug>.json`` file name."""
    stem = Path(str(name)).stem  # drop any directory parts and a .json suffix
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", stem).strip("._")
    return f"{safe or 'config'}.json"


def _config_summary(config: dict) -> dict:
    """A small, UI-friendly summary of a strategy config."""
    return {
        "strategy_id": config.get("strategy_id"),
        "symbol": config.get("symbol"),
        "timeframe": config.get("timeframe"),
        "direction_mode": config.get("direction_mode"),
        "ml_filter_enabled": bool(config.get("ml_filter_enabled", False)),
    }


def save_config(config: dict, name: Optional[str] = None) -> dict:
    """Validate and save a Strategy Lab config JSON into the configs directory.

    Validation reuses the bridge's :func:`validate_config`, so only signal-only
    compatible configs (ML disabled, long-only, supported strategy/timeframe)
    can be saved.
    """
    bridge.validate_config(config)
    if name:
        file_name = _safe_config_name(name)
    else:
        file_name = _safe_config_name(
            f"{config.get('strategy_id', 'config')}_"
            f"{config.get('symbol', 'XAUUSD')}_{config.get('timeframe', '')}"
        )
    path = configs_dir() / file_name
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return {
        "file_name": file_name,
        "path": str(path),
        "config_summary": _config_summary(config),
    }


def list_configs() -> list[dict]:
    """List saved configs (newest modified first) with a small summary each."""
    entries: list[dict] = []
    for path in configs_dir().glob("*.json"):
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue  # skip unreadable / non-JSON files
        if not isinstance(config, dict):
            continue
        stat = path.stat()
        entries.append(
            {
                "file_name": path.name,
                "path": str(path),
                "strategy_id": config.get("strategy_id"),
                "symbol": config.get("symbol"),
                "timeframe": config.get("timeframe"),
                "created_at": config.get("created_at"),
                "modified_at": datetime.fromtimestamp(
                    stat.st_mtime, timezone.utc
                ).isoformat(),
                "ml_filter_enabled": bool(config.get("ml_filter_enabled", False)),
            }
        )
    entries.sort(key=lambda item: item["modified_at"], reverse=True)
    return entries


# ---------------------------------------------------------------------------
# Config input resolution (path or inline object), with path containment
# ---------------------------------------------------------------------------
def resolve_config_path(path_str: str) -> Path:
    """Resolve a config path and require it to live inside the configs directory.

    This containment check stops the API reading arbitrary files off disk: the
    UI only ever passes back paths produced by :func:`save_config` /
    :func:`list_configs`.
    """
    base = configs_dir().resolve()
    candidate = Path(path_str)
    if not candidate.is_absolute():
        candidate = base / candidate
    candidate = candidate.resolve()
    if candidate != base and base not in candidate.parents:
        raise bridge.BridgeError(
            "config_path must be inside the MetaTrader_Data/configs directory."
        )
    if not candidate.exists():
        raise bridge.BridgeError(f"Config file not found: {candidate}")
    return candidate


def resolve_input_config(
    config: Optional[dict] = None, config_path: Optional[str] = None
) -> dict:
    """Return a validated config from an inline object or a saved-config path."""
    if config is not None:
        bridge.validate_config(config)
        return config
    if config_path:
        loaded = bridge.load_config(resolve_config_path(config_path))
        bridge.validate_config(loaded)
        return loaded
    raise bridge.BridgeError("Provide either 'config_path' or 'config'.")


# ---------------------------------------------------------------------------
# MT5 access (thin wrapper so tests can inject a fake module)
# ---------------------------------------------------------------------------
def _load_mt5():  # type: ignore[no-untyped-def]
    """Load the MetaTrader5 module via the bridge (raises a clear hint if absent)."""
    return bridge.load_mt5()


def check_mt5_readiness(
    config: dict,
    bars: int = bridge.DEFAULT_BARS,
    *,
    mt5_loader: Optional[Mt5Loader] = None,
) -> dict:
    """Check MT5 readiness for a config without emitting a signal or trading.

    Verifies, in order: the MetaTrader5 package imports, the terminal is
    reachable, account info is available, the symbol resolves, recent rates can
    be fetched, and enough closed bars exist. Returns a structured report with a
    coarse ``status`` of ``ok`` / ``warning`` / ``error``.
    """
    bridge.assert_signal_only()
    result: dict = {
        "status": "error",
        "mt5_package_available": False,
        "terminal_connected": False,
        "account_connected": False,
        "symbol": config.get("symbol"),
        "timeframe": str(config.get("timeframe", "")).upper() or None,
        "rates_available": False,
        "bars_fetched": 0,
        "latest_closed_candle_time": None,
        "message": "",
        "execution_enabled": False,
    }

    loader = mt5_loader or _load_mt5
    try:
        mt5 = loader()
    except bridge.BridgeError as exc:
        result["message"] = str(exc)
        return result
    result["mt5_package_available"] = True

    try:
        bridge.initialize_mt5(mt5)
    except bridge.BridgeError as exc:
        result["message"] = str(exc)
        return result

    try:
        result["terminal_connected"] = mt5.terminal_info() is not None
        account_info = getattr(mt5, "account_info", None)
        result["account_connected"] = bool(account_info and account_info() is not None)

        symbol = bridge.resolve_symbol(mt5, config["symbol"])
        timeframe = str(config["timeframe"]).upper()
        result["symbol"] = symbol
        result["timeframe"] = timeframe

        frame = bridge.rates_to_dataframe(
            bridge.fetch_rates(mt5, symbol, timeframe, bars)
        )
        result["rates_available"] = len(frame) > 0
        result["bars_fetched"] = int(len(frame))

        closed = bridge.select_closed_candles(frame)
        result["latest_closed_candle_time"] = pd.Timestamp(
            closed["datetime"].iloc[-1]
        ).isoformat()

        result.update(_readiness_verdict(result, closed_bars=len(closed)))
    except bridge.BridgeError as exc:
        result["status"] = "error"
        result["message"] = str(exc)
    finally:
        bridge.shutdown_mt5(mt5)
    return result


def _readiness_verdict(result: dict, *, closed_bars: int) -> dict:
    """Derive the coarse status/message from the collected readiness flags."""
    if not result["terminal_connected"]:
        return {"status": "error", "message": "MT5 terminal is not connected."}
    if closed_bars < READINESS_MIN_BARS:
        return {
            "status": "warning",
            "message": (
                f"Only {closed_bars} closed bars available "
                f"(want at least {READINESS_MIN_BARS})."
            ),
        }
    if not result["account_connected"]:
        return {
            "status": "warning",
            "message": "Terminal connected, but no account info is available yet.",
        }
    return {
        "status": "ok",
        "message": (
            f"MT5 ready: {result['symbol']} {result['timeframe']}, "
            f"{result['bars_fetched']} bars."
        ),
    }


# ---------------------------------------------------------------------------
# Run one signal check (calls the bridge core directly -- no subprocess)
# ---------------------------------------------------------------------------
def run_check_once(
    config: dict,
    bars: int = bridge.DEFAULT_BARS,
    *,
    mt5_loader: Optional[Mt5Loader] = None,
    store: Optional[SignalStore] = None,
    recent_limit: int = bridge.DEFAULT_RECENT_LIMIT,
) -> dict:
    """Run one signal-only check and persist it through the existing store.

    Returns the latest enriched signal record, the recent-candle diagnostics and
    a small stdout/stderr-style summary. ``emitted`` is False when the latest
    closed candle was already processed (one signal per candle). Execution is
    always disabled; the trading plan in the record is a reference, not an order.
    """
    bridge.assert_signal_only()
    bridge.validate_config(config)
    loader = mt5_loader or _load_mt5
    store = store or SignalStore()

    mt5 = loader()
    bridge.initialize_mt5(mt5)
    try:
        symbol = bridge.resolve_symbol(mt5, config["symbol"])
        market_context = bridge.read_market_context(mt5, symbol)
        fetch_ohlc_fn = bridge.make_fetch_ohlc_fn(mt5)
        record = bridge.run_once(
            config,
            store,
            symbol=symbol,
            fetch_ohlc_fn=fetch_ohlc_fn,
            bars=bars,
            generated_at=datetime.now(timezone.utc),
            recent_limit=recent_limit,
            market_context=market_context,
        )
    finally:
        bridge.shutdown_mt5(mt5)

    emitted = record is not None
    if emitted:
        stdout = (
            f"[{record['signal_type']}] {record['strategy_id']} {record['symbol']} "
            f"{record['timeframe']} @ {record['signal_time']} "
            f"reason={record['reason']} "
            f"(execution_enabled={record['execution_enabled']})"
        )
    else:
        stdout = f"No new closed candle / already processed ({_now_iso()})."
    return {
        "emitted": emitted,
        "signal": store.read_latest(),
        "recent_checks": store.read_recent_checks(limit=recent_limit).get("checks", []),
        "stdout": stdout,
        "stderr": "",
        "execution_enabled": False,
    }


def latest_signal() -> Optional[dict]:
    """Read the latest emitted (enriched) signal record (or ``None``)."""
    return SignalStore().read_latest()


def recent_checks(limit: int = bridge.DEFAULT_RECENT_LIMIT) -> dict:
    """Read the stored per-candle diagnostics (newest first, empty when absent)."""
    return SignalStore().read_recent_checks(limit=limit)


# ---------------------------------------------------------------------------
# Process management (start / stop / status) -- PID + state file based
# ---------------------------------------------------------------------------
def _process_state_path() -> Path:
    return state_dir() / PROCESS_STATE_FILENAME


def read_process_state() -> dict:
    """Load the persisted bridge-process state (or an empty dict)."""
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


def _pid_alive(pid: int) -> bool:
    """True if a process with ``pid`` is currently running (Windows-aware)."""
    if pid <= 0:
        return False
    if os.name == "nt":
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # exists but owned by someone else
        return True
    return True


def _pid_alive_windows(pid: int) -> bool:
    """Liveness check via the Win32 API (never uses os.kill, which would TERMINATE)."""
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return False
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def _wait_until_dead(pid: int, timeout: float) -> bool:
    """Poll until ``pid`` is no longer alive, up to ``timeout`` seconds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.2)
    return not _pid_alive(pid)


def _stop_pid(pid: int) -> None:
    """Stop a process gracefully, escalating to a forced stop only if needed."""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T"],
            capture_output=True,
            check=False,
        )
        if _wait_until_dead(pid, 5.0):
            return
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F", "/T"],
            capture_output=True,
            check=False,
        )
        return
    import signal as _signal

    try:
        os.kill(pid, _signal.SIGTERM)
    except ProcessLookupError:
        return
    if _wait_until_dead(pid, 5.0):
        return
    try:
        os.kill(pid, _signal.SIGKILL)
    except ProcessLookupError:
        pass


def _popen_kwargs() -> dict:
    """Platform-specific Popen flags to detach the poller from the parent."""
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return {"creationflags": flags}
    return {"start_new_session": True}


def process_status() -> dict:
    """Report whether the polling bridge is running, reconciling stale state.

    If the recorded process is no longer alive but the state still says
    ``running``, the state is updated to ``stopped`` so the UI never shows a
    phantom process.
    """
    state = read_process_state()
    pid = state.get("pid")
    running = bool(pid) and _pid_alive(int(pid))

    if state and not running and state.get("status") == "running":
        state["status"] = "stopped"
        state["stopped_at"] = _now_iso()
        state["stopped_reason"] = "process_exited"
        _write_process_state(state)

    return {
        "running": running,
        "pid": pid,
        "started_at": state.get("started_at"),
        "config_path": state.get("config_path"),
        "poll_seconds": state.get("poll_seconds"),
        "bars": state.get("bars"),
        "status": "running" if running else state.get("status", "stopped"),
        "execution_enabled": False,
    }


def start_polling(
    config_path: str,
    poll_seconds: int = 60,
    bars: int = bridge.DEFAULT_BARS,
) -> dict:
    """Start the signal-only polling subprocess, refusing to start a duplicate."""
    current = process_status()
    if current["running"]:
        return {**current, "started": False, "message": "Bridge already running."}

    resolved = resolve_config_path(config_path)
    bridge.validate_config(bridge.load_config(resolved))

    directory = state_dir()
    stdout_path = directory / STDOUT_LOG_FILENAME
    stderr_path = directory / STDERR_LOG_FILENAME

    command = [
        sys.executable,
        str(RUN_SCRIPT),
        "--config",
        str(resolved),
        "--poll-seconds",
        str(int(poll_seconds)),
        "--bars",
        str(int(bars)),
        "--output-dir",
        str(directory),
    ]

    # Truncate logs so the UI tail reflects the current run; the child keeps the
    # inherited handles after we close our own copies.
    with stdout_path.open("w", encoding="utf-8") as out, stderr_path.open(
        "w", encoding="utf-8"
    ) as err:
        process = subprocess.Popen(  # noqa: S603 - fixed args, no shell
            command,
            stdout=out,
            stderr=err,
            cwd=str(REPO_ROOT),
            **_popen_kwargs(),
        )

    state = {
        "pid": process.pid,
        "started_at": _now_iso(),
        "config_path": str(resolved),
        "poll_seconds": int(poll_seconds),
        "bars": int(bars),
        "status": "running",
    }
    _write_process_state(state)
    return {**process_status(), "started": True, "message": "Bridge polling started."}


def stop_polling() -> dict:
    """Stop the managed polling subprocess and update the state file."""
    state = read_process_state()
    pid = state.get("pid")

    if not pid or not _pid_alive(int(pid)):
        if state:
            state["status"] = "stopped"
            state.setdefault("stopped_at", _now_iso())
            _write_process_state(state)
        return {**process_status(), "stopped": True, "message": "Bridge not running."}

    _stop_pid(int(pid))
    state["status"] = "stopped"
    state["stopped_at"] = _now_iso()
    _write_process_state(state)
    return {**process_status(), "stopped": True, "message": "Bridge stopped."}


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------
def _tail_file(path: Path, lines: int) -> str:
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def tail_logs(lines: int = 100) -> dict:
    """Return the last ``lines`` of the bridge stdout/stderr logs."""
    directory = state_dir()
    return {
        "stdout_tail": _tail_file(directory / STDOUT_LOG_FILENAME, lines),
        "stderr_tail": _tail_file(directory / STDERR_LOG_FILENAME, lines),
    }


def bridge_status(log_excerpt_lines: int = 20) -> dict:
    """Rich status for the UI: process status + latest signal + recent checks + logs."""
    status = process_status()
    latest = latest_signal()
    checks = recent_checks()
    logs = tail_logs(log_excerpt_lines)
    status.update(
        {
            "latest_signal": latest,
            "latest_signal_time": latest.get("signal_time") if latest else None,
            "recent_checks": checks.get("checks", []),
            "latest_log_excerpt": logs["stdout_tail"],
            "error_excerpt": logs["stderr_tail"] or None,
        }
    )
    return status
