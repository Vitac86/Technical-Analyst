"""Local, file-based persistence for the v1.7 MT5 signal-only bridge.

State, the signal log and the latest signal all live as plain files under an
output directory (default ``MetaTrader_Data/reports/mt5_signal_bridge/``, which
is git-ignored). Nothing here touches MT5, places orders, or stores credentials.

Files written:

    * ``state.json``         -- per (strategy, symbol, timeframe): the last
      processed ``signal_time`` and last emitted ``signal_id`` (one-signal-per-
      candle deduplication).
    * ``signals.csv``        -- append-only log of emitted signals, one row per
      processed closed candle (flattened :data:`SIGNAL_CSV_FIELDS` columns,
      including the human reason and buy-zone distance diagnostics).
    * ``latest_signal.json`` -- the most recently emitted *enriched* signal record
      (full ``market_snapshot`` / ``strategy_state`` / ``trading_plan`` objects).
    * ``recent_checks.json`` -- per-candle diagnostics over the latest N closed
      candles (a display aid; emits no official signal).

The same files are read back by the optional read-only signals API.
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Optional

import pandas as pd

try:  # package import
    from .mt5_signal_bridge import SIGNAL_CSV_FIELDS, flatten_signal_for_csv
except ImportError:  # script import
    from mt5_signal_bridge import (  # type: ignore[no-redef]
        SIGNAL_CSV_FIELDS,
        flatten_signal_for_csv,
    )

# Repo root: signal_store.py -> strategy_lab -> app -> backend -> ROOT
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "MetaTrader_Data" / "reports" / "mt5_signal_bridge"

STATE_FILENAME = "state.json"
SIGNALS_FILENAME = "signals.csv"
LATEST_FILENAME = "latest_signal.json"
RECENT_CHECKS_FILENAME = "recent_checks.json"

# Env override lets the bridge and the API agree on a non-default location.
_ENV_OUTPUT_DIR = "MT5_SIGNAL_BRIDGE_DIR"


def default_output_dir() -> Path:
    """Resolve the default output directory (env override wins)."""
    override = os.environ.get(_ENV_OUTPUT_DIR)
    return Path(override) if override else DEFAULT_OUTPUT_DIR


class SignalStore:
    """Read/write the bridge's local state, signal log and latest signal."""

    def __init__(self, output_dir: str | Path | None = None) -> None:
        self.output_dir = Path(output_dir) if output_dir else default_output_dir()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.output_dir / STATE_FILENAME
        self.signals_path = self.output_dir / SIGNALS_FILENAME
        self.latest_path = self.output_dir / LATEST_FILENAME
        self.recent_checks_path = self.output_dir / RECENT_CHECKS_FILENAME

    # -- keys ---------------------------------------------------------------
    @staticmethod
    def make_key(strategy_id: str, symbol: str, timeframe: str) -> str:
        """Composite dedup key for a (strategy, symbol, timeframe) stream."""
        return f"{strategy_id}|{symbol}|{timeframe.upper()}"

    # -- state --------------------------------------------------------------
    def load_state(self) -> dict:
        """Load the persisted state, or an empty skeleton when absent."""
        if not self.state_path.exists():
            return {"version": 1, "entries": {}}
        try:
            with self.state_path.open("r", encoding="utf-8") as handle:
                state = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return {"version": 1, "entries": {}}
        state.setdefault("entries", {})
        return state

    def _save_state(self, state: dict) -> None:
        with self.state_path.open("w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)

    def last_processed_signal_time(self, key: str) -> Optional[pd.Timestamp]:
        """The last processed candle time for ``key`` (or ``None``)."""
        entry = self.load_state()["entries"].get(key)
        if not entry or not entry.get("last_processed_signal_time"):
            return None
        return pd.Timestamp(entry["last_processed_signal_time"])

    def already_processed(self, key: str, signal_time: pd.Timestamp) -> bool:
        """True if ``signal_time`` is not newer than the last processed candle."""
        last = self.last_processed_signal_time(key)
        if last is None:
            return False
        return pd.Timestamp(signal_time) <= last

    # -- recording ----------------------------------------------------------
    def record(self, key: str, signal: dict) -> None:
        """Persist one emitted signal: append to the log, refresh latest, bump state.

        Writing the log + latest before the state update means a crash leaves a
        recoverable log rather than a silently-skipped candle.
        """
        self._append_signal_row(signal)
        self.write_latest(signal)

        state = self.load_state()
        state["entries"][key] = {
            "last_processed_signal_time": signal["signal_time"],
            "last_signal_id": signal["signal_id"],
            "last_signal_type": signal["signal_type"],
            "updated_at": signal["generated_at"],
        }
        self._save_state(state)

    def _append_signal_row(self, signal: dict) -> None:
        """Append the flattened signal, including v1.7.3 buy-zone diagnostics."""
        row = flatten_signal_for_csv(signal)
        write_header = not self.signals_path.exists()
        with self.signals_path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(SIGNAL_CSV_FIELDS))
            if write_header:
                writer.writeheader()
            writer.writerow({field: row.get(field) for field in SIGNAL_CSV_FIELDS})

    def write_latest(self, signal: dict) -> None:
        """Overwrite ``latest_signal.json`` with the most recent enriched signal."""
        with self.latest_path.open("w", encoding="utf-8") as handle:
            json.dump(signal, handle, indent=2)

    # -- recent checks (per-candle diagnostics; not official signals) --------
    def write_recent_checks(self, payload: dict) -> None:
        """Overwrite ``recent_checks.json`` with the latest per-candle diagnostics."""
        with self.recent_checks_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)

    def read_recent_checks(self, limit: Optional[int] = None) -> dict:
        """Return the stored recent-candle diagnostics (newest first).

        Returns an empty skeleton (``{"checks": []}``) when no checks have been
        written yet, so callers never have to special-case a missing file.
        """
        empty = {
            "generated_at": None,
            "symbol": None,
            "timeframe": None,
            "strategy_id": None,
            "checks": [],
        }
        if not self.recent_checks_path.exists():
            return empty
        try:
            with self.recent_checks_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return empty
        if not isinstance(payload, dict):
            return empty
        checks = payload.get("checks")
        if not isinstance(checks, list):
            checks = []
        if limit is not None and limit >= 0:
            checks = checks[:limit]
        payload["checks"] = checks
        return payload

    # -- reading (used by the read-only signals API) ------------------------
    def read_latest(self) -> Optional[dict]:
        """Return the latest signal record, or ``None`` if none has been emitted."""
        if not self.latest_path.exists():
            return None
        try:
            with self.latest_path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None

    def read_history(self, limit: int = 50) -> list[dict]:
        """Return up to ``limit`` most-recent signals (newest first)."""
        if not self.signals_path.exists():
            return []
        with self.signals_path.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        rows.reverse()  # newest first
        if limit is not None and limit >= 0:
            rows = rows[:limit]
        return rows
