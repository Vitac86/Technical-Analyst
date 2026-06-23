"""Strategy Lab v1.7: read-only signal endpoints (signal-only bridge output).

These endpoints simply read the local files written by the independent
``run_mt5_signal_bridge.py`` process (``latest_signal.json`` / ``signals.csv``).
The bridge does **not** need to run inside FastAPI; it is a separate process that
writes files which these endpoints read.

There are intentionally **no** order-execution endpoints. v1.7 is signal-only.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.strategy_lab.signal_store import SignalStore

router = APIRouter()


def _store() -> SignalStore:
    """Open the signal store at the configured/default output directory."""
    return SignalStore()


@router.get("/latest")
def get_latest_signal() -> dict:
    """Return the most recently emitted signal (or ``null`` if none yet)."""
    latest = _store().read_latest()
    return {"signal": latest, "execution_enabled": False}


@router.get("/history")
def get_signal_history(
    limit: int = Query(default=50, ge=1, le=1000),
) -> dict:
    """Return up to ``limit`` most-recent signals (newest first)."""
    signals = _store().read_history(limit=limit)
    return {
        "signals": signals,
        "count": len(signals),
        "execution_enabled": False,
    }
