"""Strategy Lab v1.8: local, file-based persistence for the **demo execution robot**.

This store is the execution-side counterpart of
:mod:`app.strategy_lab.signal_store` (which serves the signal-only bridge). It is
a **separate** store with its **own** output directory so the signal-only bridge
output is never touched by the execution robot.

Files written under ``MetaTrader_Data/reports/mt5_execution_robot/`` (git-ignored):

    * ``execution_state.json``            -- the last *open-attempt* signal_time and
      decision id (open-attempt deduplication: one BUY per closed candle).
    * ``execution_events.csv``            -- append-only log, one row per decision
      (dry-run, demo execution or refusal), flattened to the history columns.
    * ``latest_execution_decision.json``  -- the most recent full decision record
      (account / market / sizing / position / order / trailing diagnostics).
    * ``robot_stdout.log`` / ``robot_stderr.log`` -- written by the polling
      subprocess (see :mod:`app.strategy_lab.mt5_execution_manager`).

Nothing in this module imports MetaTrader5 or sends orders -- it only reads and
writes plain files.
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Optional

import pandas as pd

# Repo root: execution_store.py -> strategy_lab -> app -> backend -> ROOT
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_DIR = (
    REPO_ROOT / "MetaTrader_Data" / "reports" / "mt5_execution_robot"
)

STATE_FILENAME = "execution_state.json"
EVENTS_FILENAME = "execution_events.csv"
LATEST_FILENAME = "latest_execution_decision.json"
STDOUT_LOG_FILENAME = "robot_stdout.log"
STDERR_LOG_FILENAME = "robot_stderr.log"

# Env override lets the robot and the API/tests agree on a non-default location.
_ENV_OUTPUT_DIR = "MT5_EXECUTION_ROBOT_DIR"

# Flattened columns for execution_events.csv (mirrors the UI history table).
EVENT_CSV_FIELDS: tuple[str, ...] = (
    "decision_id",
    "generated_at",
    "mode",
    "intended_action",
    "signal_time",
    "signal_type",
    "symbol",
    "lot",
    "entry_price",
    "initial_stop_price",
    "order_retcode",
    "refusal_reasons",
)


def default_output_dir() -> Path:
    """Resolve the default execution-robot output directory (env override wins)."""
    override = os.environ.get(_ENV_OUTPUT_DIR)
    return Path(override) if override else DEFAULT_OUTPUT_DIR


class ExecutionStore:
    """Read/write the execution robot's state, event log and latest decision."""

    def __init__(self, output_dir: str | Path | None = None) -> None:
        self.output_dir = Path(output_dir) if output_dir else default_output_dir()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.output_dir / STATE_FILENAME
        self.events_path = self.output_dir / EVENTS_FILENAME
        self.latest_path = self.output_dir / LATEST_FILENAME
        self.stdout_log_path = self.output_dir / STDOUT_LOG_FILENAME
        self.stderr_log_path = self.output_dir / STDERR_LOG_FILENAME

    # -- keys ---------------------------------------------------------------
    @staticmethod
    def make_key(strategy_id: str, symbol: str, timeframe: str) -> str:
        """Composite dedup key for a (strategy, symbol, timeframe) stream."""
        return f"{strategy_id}|{symbol}|{timeframe.upper()}"

    # -- state (open-attempt deduplication) ---------------------------------
    def load_state(self) -> dict:
        """Load the persisted state, or an empty skeleton when absent."""
        if not self.state_path.exists():
            return {"version": 1, "entries": {}}
        try:
            with self.state_path.open("r", encoding="utf-8") as handle:
                state = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return {"version": 1, "entries": {}}
        if not isinstance(state, dict):
            return {"version": 1, "entries": {}}
        state.setdefault("entries", {})
        return state

    def _save_state(self, state: dict) -> None:
        with self.state_path.open("w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)

    def last_processed_open_signal_time(self, key: str) -> Optional[pd.Timestamp]:
        """The last signal_time we *attempted an open* for (or ``None``)."""
        entry = self.load_state()["entries"].get(key)
        if not entry or not entry.get("latest_processed_signal_time"):
            return None
        return pd.Timestamp(entry["latest_processed_signal_time"])

    def already_opened_for_signal(self, key: str, signal_time: pd.Timestamp) -> bool:
        """True if a BUY open has already been attempted for ``signal_time``.

        This is the duplicate-order guard: once we send (or simulate sending) an
        entry for a closed candle, the same candle must never trigger a second
        order.
        """
        last = self.last_processed_open_signal_time(key)
        if last is None:
            return False
        return pd.Timestamp(signal_time) <= last

    def mark_open_processed(
        self, key: str, signal_time: pd.Timestamp, decision_id: str, generated_at: str
    ) -> None:
        """Record that a BUY open was attempted for ``signal_time`` (dedup guard)."""
        state = self.load_state()
        state["entries"][key] = {
            "latest_processed_signal_time": pd.Timestamp(signal_time).isoformat(),
            "last_open_decision_id": decision_id,
            "updated_at": generated_at,
        }
        self._save_state(state)

    # -- recording a decision ----------------------------------------------
    def write_decision(self, decision: dict) -> None:
        """Persist one decision: refresh latest, append the event log row.

        Writing the latest record and the CSV row covers every decision (dry-run,
        demo execution or refusal). Open-attempt dedup is bumped separately via
        :meth:`mark_open_processed` so a refused/no-action decision never blocks a
        later genuine entry on the same candle.
        """
        self.write_latest(decision)
        self._append_event_row(decision)

    def write_latest(self, decision: dict) -> None:
        """Overwrite ``latest_execution_decision.json`` with the latest decision."""
        with self.latest_path.open("w", encoding="utf-8") as handle:
            json.dump(decision, handle, indent=2)

    def _append_event_row(self, decision: dict) -> None:
        row = flatten_decision_for_csv(decision)
        write_header = not self.events_path.exists()
        with self.events_path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(EVENT_CSV_FIELDS))
            if write_header:
                writer.writeheader()
            writer.writerow({field: row.get(field) for field in EVENT_CSV_FIELDS})

    # -- reading ------------------------------------------------------------
    def read_latest(self) -> Optional[dict]:
        """Return the latest decision record, or ``None`` if none has been made."""
        if not self.latest_path.exists():
            return None
        try:
            with self.latest_path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None

    def read_history(self, limit: int = 50) -> list[dict]:
        """Return up to ``limit`` most-recent decisions (newest first)."""
        if not self.events_path.exists():
            return []
        with self.events_path.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        rows.reverse()  # newest first
        if limit is not None and limit >= 0:
            rows = rows[:limit]
        return rows


def flatten_decision_for_csv(decision: dict) -> dict:
    """Flatten a decision record to the :data:`EVENT_CSV_FIELDS` columns."""
    signal = decision.get("signal") or {}
    sizing = decision.get("sizing") or {}
    order_result = decision.get("order_result") or {}
    refusal_reasons = decision.get("refusal_reasons") or []
    return {
        "decision_id": decision.get("decision_id"),
        "generated_at": decision.get("generated_at"),
        "mode": decision.get("mode"),
        "intended_action": decision.get("intended_action"),
        "signal_time": signal.get("signal_time"),
        "signal_type": signal.get("signal_type"),
        "symbol": decision.get("symbol"),
        "lot": sizing.get("rounded_lot"),
        "entry_price": sizing.get("entry_price"),
        "initial_stop_price": sizing.get("initial_stop_price"),
        "order_retcode": order_result.get("retcode"),
        "refusal_reasons": ";".join(str(r) for r in refusal_reasons),
    }
