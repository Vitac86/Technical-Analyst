"""Strategy Lab v1.6 service layer: run, compare and export rule-based presets.

This module is the orchestration glue between the FastAPI endpoints and the
existing research modules. It performs **no** new backtesting maths: trade
simulation is delegated to :mod:`app.strategy_lab.risk_backtester`, account
metrics to :mod:`app.strategy_lab.metrics`, and the per-period metric helpers
(walk-forward, yearly) are reused from
:mod:`app.strategy_lab.run_finalist_confirmation`.

Responsibilities:

    * resolve and cache the read-only MT5 CSV for a (symbol, timeframe),
    * build a :class:`RiskConfig` from a preset + validated user overrides,
    * run one backtest and serialise compact, size-bounded results for the UI
      (downsampled equity/drawdown, a capped trades table, yearly + walk-forward
      period summaries),
    * compare several configs side by side,
    * produce a portable JSON strategy config for a later MT5 robot / signal
      bridge.

It never modifies the source CSVs and never writes generated CSV outputs.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

try:  # package import
    from . import data_loader, indicators, presets, risk_backtester
    from .presets import StrategyPreset
    from .run_finalist_confirmation import (
        ACCOUNT_DEFAULTS,
        COST_SCENARIOS,
        COST_SCENARIO_ORDER,
        WALK_FORWARD_SPLITS,
        _period_local_drawdown,
        _slice_by_time,
        compute_period_metrics,
    )
except ImportError:  # script import
    import data_loader  # type: ignore[no-redef]
    import indicators  # type: ignore[no-redef]
    import presets  # type: ignore[no-redef]
    import risk_backtester  # type: ignore[no-redef]
    from presets import StrategyPreset  # type: ignore[no-redef]
    from run_finalist_confirmation import (  # type: ignore[no-redef]
        ACCOUNT_DEFAULTS,
        COST_SCENARIOS,
        COST_SCENARIO_ORDER,
        WALK_FORWARD_SPLITS,
        _period_local_drawdown,
        _slice_by_time,
        compute_period_metrics,
    )

CONFIG_VERSION = "1.6"

# Repo root: lab_service.py -> strategy_lab -> app -> backend -> ROOT
REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "MetaTrader_Data" / "mt5_exports"

# Output-size guards (requirement 4): keep API payloads small by default.
DEFAULT_TRADES_LIMIT = 500
MAX_TRADES_LIMIT = 5000
DEFAULT_EQUITY_POINTS = 1500
MAX_EQUITY_POINTS = 6000
MAX_COMPARE_CONFIGS = 8

# Cost knobs handed to RiskConfig for a "Custom" scenario.
_COST_KEYS: tuple[str, ...] = (
    "fixed_spread_points",
    "slippage_points",
    "commission_per_lot_round_turn",
    "swap_long_per_lot_per_day",
    "swap_short_per_lot_per_day",
)

# Period-summary fields surfaced to the UI (requirement 1.I).
_PERIOD_FIELDS: tuple[str, ...] = (
    "return_pct",
    "max_drawdown_pct",
    "trades",
    "profit_factor",
    "net_profit",
)


class LabError(ValueError):
    """User-facing error (bad params, unknown preset/scenario). -> HTTP 422."""


class DataUnavailableError(RuntimeError):
    """The read-only market data for a (symbol, timeframe) is not present."""


# ---------------------------------------------------------------------------
# Data loading (cached, read-only)
# ---------------------------------------------------------------------------
def _resolve_data_path(symbol: str, timeframe: str) -> Path:
    """Locate the MT5 CSV for ``symbol``/``timeframe`` (read-only).

    Tries the exact and ``<symbol>rfd`` naming first, then falls back to any
    ``*_<TF>.csv`` whose symbol root matches, so callers can pass the clean
    symbol (``XAUUSD``) while the file is ``XAUUSDrfd_H4.csv``.
    """
    tf = timeframe.upper()
    sym = symbol.strip()
    for name in (f"{sym}_{tf}.csv", f"{sym}rfd_{tf}.csv"):
        candidate = DATA_DIR / name
        if candidate.exists():
            return candidate

    needle = sym.upper()
    if DATA_DIR.exists():
        for path in sorted(DATA_DIR.glob(f"*_{tf}.csv")):
            file_symbol = path.stem.split("_")[0].upper()
            if file_symbol.startswith(needle) or needle in file_symbol:
                return path

    raise DataUnavailableError(
        f"No market data found for symbol '{symbol}' timeframe '{timeframe}' "
        f"under {DATA_DIR}."
    )


@lru_cache(maxsize=8)
def _load_cached(path_str: str) -> pd.DataFrame:
    """Load + cache one CSV (keyed by absolute path string)."""
    return data_loader.load_mt5_csv(path_str)


def load_ohlc(symbol: str, timeframe: str) -> pd.DataFrame:
    """Return the clean OHLC frame for ``symbol``/``timeframe`` (cached)."""
    path = _resolve_data_path(symbol, timeframe)
    return _load_cached(str(path))


def available_symbols() -> list[str]:
    """Distinct symbol roots discoverable under the data directory."""
    if not DATA_DIR.exists():
        return []
    roots = {p.stem.split("_")[0] for p in DATA_DIR.glob("*.csv")}
    return sorted(roots)


# ---------------------------------------------------------------------------
# Small numeric/serialisation helpers
# ---------------------------------------------------------------------------
def _clean(value: object) -> Optional[float]:
    """JSON-safe float: NaN/inf -> None (so the payload is strict JSON)."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _epoch_seconds(dt_series: pd.Series) -> np.ndarray:
    """UTC epoch seconds for a datetime64 column (naive wall time == UTC here)."""
    ns = dt_series.to_numpy("datetime64[ns]").astype("int64")
    return ns // 1_000_000_000


def _iso(value: object) -> Optional[str]:
    """ISO-8601 string for a timestamp-like value, or None."""
    if value is None:
        return None
    ts = pd.Timestamp(value)
    if pd.isna(ts):
        return None
    return ts.isoformat()


def _downsample_indices(n: int, max_points: int) -> np.ndarray:
    """Evenly-strided indices (always keeping the last) capped at ``max_points``."""
    if n <= max_points:
        return np.arange(n)
    idx = np.linspace(0, n - 1, num=max_points).astype(int)
    idx[-1] = n - 1
    return np.unique(idx)


# ---------------------------------------------------------------------------
# Cost scenarios
# ---------------------------------------------------------------------------
def resolve_cost_kwargs(scenario: str, custom_costs: Optional[dict]) -> dict:
    """Return the RiskConfig cost kwargs for a named or custom scenario."""
    if scenario == "Custom":
        if not custom_costs:
            raise LabError("cost_scenario 'Custom' requires custom_costs.")
        return {k: float(custom_costs.get(k, 0.0)) for k in _COST_KEYS}
    if scenario not in COST_SCENARIOS:
        valid = ", ".join([*COST_SCENARIO_ORDER, "Custom"])
        raise LabError(f"Unknown cost_scenario '{scenario}'. Valid: {valid}.")
    return dict(COST_SCENARIOS[scenario])


def cost_scenarios_catalogue() -> list[dict]:
    """Built-in cost scenarios (Base/Conservative/Stress) for the UI."""
    catalogue: list[dict] = []
    for name in COST_SCENARIO_ORDER:
        catalogue.append({"name": name, **COST_SCENARIOS[name]})
    return catalogue


# ---------------------------------------------------------------------------
# Core run
# ---------------------------------------------------------------------------
def _date_bounds(
    start: Optional[str], end: Optional[str]
) -> tuple[Optional[pd.Timestamp], Optional[pd.Timestamp]]:
    """Inclusive naive-UTC [start 00:00, end 23:59:59] bounds from date strings."""
    start_ts = pd.Timestamp(start) if start else None
    end_ts = (
        pd.Timestamp(end) + pd.Timedelta(hours=23, minutes=59, seconds=59)
        if end
        else None
    )
    return start_ts, end_ts


def _period_row(
    label: str,
    start: Optional[pd.Timestamp],
    end: Optional[pd.Timestamp],
    trades: pd.DataFrame,
    equity: pd.DataFrame,
    skipped: pd.DataFrame,
) -> Optional[dict]:
    """One yearly/walk-forward row, or None when the period has no bars."""
    ec_slice = _slice_by_time(equity, "datetime", start, end)
    if not len(ec_slice):
        return None
    trades_sub = _slice_by_time(trades, "exit_time", start, end)
    skipped_sub = (
        _slice_by_time(skipped, "entry_time", start, end) if len(skipped) else skipped
    )
    start_equity = float(ec_slice["equity"].iloc[0])
    m = compute_period_metrics(
        trades_sub, ec_slice, skipped_sub, period_start_equity=start_equity
    )
    return {
        "period": label,
        "return_pct": _clean(m["total_return_pct"]),
        "max_drawdown_pct": _clean(m["max_drawdown_pct"]),
        "trades": int(m["total_trades"]),
        "profit_factor": _clean(m["profit_factor"]),
        "net_profit": _clean(m["net_profit"]),
    }


def _yearly_summary(
    trades: pd.DataFrame, equity: pd.DataFrame, skipped: pd.DataFrame
) -> list[dict]:
    """Per-calendar-year period rows across the equity curve's span."""
    if not len(equity):
        return []
    years = range(
        int(equity["datetime"].iloc[0].year), int(equity["datetime"].iloc[-1].year) + 1
    )
    rows: list[dict] = []
    for year in years:
        start = pd.Timestamp(f"{year}-01-01")
        end = pd.Timestamp(f"{year}-12-31 23:59:59")
        row = _period_row(str(year), start, end, trades, equity, skipped)
        if row is not None:
            rows.append(row)
    return rows


def _walk_forward_summary(
    trades: pd.DataFrame, equity: pd.DataFrame, skipped: pd.DataFrame
) -> list[dict]:
    """Walk-forward period rows using the confirmed v1.4 split boundaries."""
    rows: list[dict] = []
    for label, lo, hi in WALK_FORWARD_SPLITS:
        start = pd.Timestamp(f"{lo}-01-01")
        end = pd.Timestamp(f"{hi}-12-31 23:59:59")
        row = _period_row(label, start, end, trades, equity, skipped)
        if row is not None:
            rows.append(row)
    return rows


def _summary_metrics(
    trades: pd.DataFrame,
    equity: pd.DataFrame,
    skipped: pd.DataFrame,
    *,
    initial_equity: float,
) -> dict:
    """The headline metric set (reuses compute_period_metrics over the view)."""
    if not len(equity):
        start_equity = initial_equity
    else:
        start_equity = float(equity["equity"].iloc[0])
    m = compute_period_metrics(
        trades, equity, skipped, period_start_equity=start_equity
    )
    keys = (
        "initial_equity",
        "final_equity",
        "total_return_pct",
        "net_profit",
        "profit_factor",
        "max_drawdown_pct",
        "total_trades",
        "winning_trades",
        "losing_trades",
        "win_rate",
        "average_r",
        "median_r",
        "average_lots",
        "max_effective_leverage",
        "average_effective_leverage",
        "stop_out_count",
        "insufficient_margin_count",
    )
    out: dict = {}
    for key in keys:
        value = m.get(key)
        if key in ("total_trades", "winning_trades", "losing_trades",
                   "stop_out_count", "insufficient_margin_count"):
            out[key] = int(value) if value is not None else 0
        else:
            out[key] = _clean(value)
    return out


def _serialise_equity(equity: pd.DataFrame, max_points: int) -> tuple[list[dict], list[dict]]:
    """Downsampled (equity_curve, drawdown_series) for charting."""
    if not len(equity):
        return [], []
    local = _period_local_drawdown(equity)
    idx = _downsample_indices(len(local), max_points)
    sub = local.iloc[idx]
    times = _epoch_seconds(sub["datetime"])
    balance = sub["balance"].to_numpy(dtype=float)
    eq = sub["equity"].to_numpy(dtype=float)
    dd = sub["drawdown_pct"].to_numpy(dtype=float)

    equity_curve = [
        {"t": int(t), "equity": _clean(e), "balance": _clean(b)}
        for t, e, b in zip(times, eq, balance)
    ]
    drawdown_series = [
        {"t": int(t), "drawdown_pct": _clean(d)} for t, d in zip(times, dd)
    ]
    return equity_curve, drawdown_series


def _serialise_trades(trades: pd.DataFrame, limit: int) -> tuple[list[dict], int, bool]:
    """Map the trades frame to the compact UI columns, capped at ``limit`` rows."""
    total = int(len(trades))
    if total == 0:
        return [], 0, False
    capped = trades.head(limit)
    rows: list[dict] = []
    for row in capped.itertuples(index=False):
        rows.append(
            {
                "entry_time": _iso(row.entry_time),
                "exit_time": _iso(row.exit_time),
                "direction": row.direction,
                "lots": _clean(row.lots),
                "entry_price": _clean(row.entry_price),
                "exit_price": _clean(row.exit_price),
                "exit_reason": row.exit_reason,
                "bars_held": int(row.bars_held),
                "net_pnl": _clean(row.net_pnl),
                "r_multiple": _clean(row.r_multiple),
                "balance_after_trade": _clean(row.balance_after_trade),
            }
        )
    return rows, total, total > limit


def _run_full_backtest(
    preset: StrategyPreset,
    merged: dict,
    cost_kwargs: dict,
    symbol: str,
    timeframe: str,
    df: Optional[pd.DataFrame],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Load data, build the config and run one backtest over the full history."""
    if df is None:
        df = load_ohlc(symbol, timeframe)
    config = presets.build_risk_config(preset, merged, cost_kwargs, dict(ACCOUNT_DEFAULTS))
    signals = presets.generate_signals(preset, df, merged)
    atr_values = indicators.atr(df, config.atr_period).to_numpy(dtype=float)
    trades, equity, skipped = risk_backtester.run_risk_backtest(
        df,
        signals,
        config,
        strategy_name=preset.preset_id,
        symbol=symbol,
        timeframe=timeframe,
        atr_values=atr_values,
    )
    return df, trades, equity, skipped


def prepare_run(
    *,
    preset_id: str,
    symbol: str,
    timeframe: Optional[str],
    overrides: dict,
    cost_scenario: str,
    custom_costs: Optional[dict],
) -> tuple[StrategyPreset, dict, str, dict]:
    """Validate inputs and return (preset, merged_params, timeframe, cost_kwargs)."""
    try:
        preset = presets.get_preset(preset_id)
    except KeyError as exc:
        raise LabError(str(exc)) from exc

    merged = presets.merge_parameters(preset, overrides)
    errors = presets.validate_parameters(preset, merged)
    if errors:
        raise LabError("; ".join(errors))

    cost_kwargs = resolve_cost_kwargs(cost_scenario, custom_costs)
    tf = (timeframe or preset.timeframe).upper()
    return preset, merged, tf, cost_kwargs


def run_backtest(
    *,
    preset_id: str,
    symbol: str = "XAUUSD",
    timeframe: Optional[str] = None,
    overrides: Optional[dict] = None,
    cost_scenario: str = "Base",
    custom_costs: Optional[dict] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    trades_limit: int = DEFAULT_TRADES_LIMIT,
    equity_points: int = DEFAULT_EQUITY_POINTS,
    df: Optional[pd.DataFrame] = None,
) -> dict:
    """Run one backtest and return a compact, JSON-safe result dict.

    ``df`` may be injected (tests) to avoid touching the read-only CSVs.
    """
    overrides = overrides or {}
    preset, merged, tf, cost_kwargs = prepare_run(
        preset_id=preset_id,
        symbol=symbol,
        timeframe=timeframe,
        overrides=overrides,
        cost_scenario=cost_scenario,
        custom_costs=custom_costs,
    )

    trades_limit = max(1, min(int(trades_limit), MAX_TRADES_LIMIT))
    equity_points = max(50, min(int(equity_points), MAX_EQUITY_POINTS))

    _df, trades, equity, skipped = _run_full_backtest(
        preset, merged, cost_kwargs, symbol, tf, df
    )

    # Apply the optional reporting window to the *outputs* (so indicator warmup
    # always uses full history; mirrors the research runner's period slicing).
    start_ts, end_ts = _date_bounds(start, end)
    equity_view = _slice_by_time(equity, "datetime", start_ts, end_ts)
    trades_view = _slice_by_time(trades, "exit_time", start_ts, end_ts)
    skipped_view = (
        _slice_by_time(skipped, "entry_time", start_ts, end_ts)
        if len(skipped)
        else skipped
    )

    summary = _summary_metrics(
        trades_view, equity_view, skipped_view, initial_equity=merged["initial_equity"]
    )
    equity_curve, drawdown_series = _serialise_equity(equity_view, equity_points)
    trade_rows, trades_total, trades_truncated = _serialise_trades(
        trades_view, trades_limit
    )

    strategy_params, exit_params, risk_params = presets.split_parameters(preset, merged)

    data_range = {
        "start": _iso(equity_view["datetime"].iloc[0]) if len(equity_view) else None,
        "end": _iso(equity_view["datetime"].iloc[-1]) if len(equity_view) else None,
        "bars": int(len(equity_view)),
    }

    warnings: list[str] = []
    if trades_total == 0:
        warnings.append("No trades were generated for this configuration / window.")

    return {
        "preset_id": preset.preset_id,
        "display_name": preset.display_name,
        "symbol": symbol,
        "timeframe": tf,
        "strategy_name": preset.strategy_name,
        "direction_mode": preset.direction_mode,
        "exit_mode": preset.exit_mode,
        "sizing_mode": preset.sizing_mode,
        "cost_scenario": cost_scenario,
        "cost_assumptions": {k: _clean(v) for k, v in cost_kwargs.items()},
        "parameters": {
            "strategy": strategy_params,
            "exit": exit_params,
            "risk": risk_params,
        },
        "summary": summary,
        "equity_curve": equity_curve,
        "drawdown_series": drawdown_series,
        "trades": trade_rows,
        "trades_total": trades_total,
        "trades_truncated": trades_truncated,
        "yearly_summary": _yearly_summary(trades_view, equity_view, skipped_view),
        "walk_forward_summary": _walk_forward_summary(
            trades_view, equity_view, skipped_view
        ),
        "data_range": data_range,
        "warnings": warnings,
        "research_disclaimer": presets.RESEARCH_DISCLAIMER,
        "ml_note": presets.ML_RESEARCH_NOTE,
    }


# ---------------------------------------------------------------------------
# Compare (requirement C)
# ---------------------------------------------------------------------------
_COMPARE_FIELDS: tuple[str, ...] = (
    "final_equity",
    "total_return_pct",
    "net_profit",
    "profit_factor",
    "max_drawdown_pct",
    "total_trades",
    "win_rate",
    "average_r",
    "median_r",
    "average_lots",
    "max_effective_leverage",
    "stop_out_count",
    "insufficient_margin_count",
)


def compare_configs(configs: list[dict]) -> dict:
    """Run each config and return side-by-side comparison metrics."""
    if not configs:
        raise LabError("compare requires at least one config.")
    if len(configs) > MAX_COMPARE_CONFIGS:
        raise LabError(f"compare accepts at most {MAX_COMPARE_CONFIGS} configs.")

    rows: list[dict] = []
    for index, cfg in enumerate(configs):
        result = run_backtest(
            preset_id=cfg["preset_id"],
            symbol=cfg.get("symbol", "XAUUSD"),
            timeframe=cfg.get("timeframe"),
            overrides=cfg.get("overrides") or {},
            cost_scenario=cfg.get("cost_scenario", "Base"),
            custom_costs=cfg.get("custom_costs"),
            start=cfg.get("start"),
            end=cfg.get("end"),
            trades_limit=1,  # we only need the summary metrics here
            equity_points=50,
        )
        summary = result["summary"]
        label = cfg.get("label") or f"{result['preset_id']} #{index + 1}"
        rows.append(
            {
                "label": label,
                "preset_id": result["preset_id"],
                "timeframe": result["timeframe"],
                "cost_scenario": result["cost_scenario"],
                "metrics": {field: summary.get(field) for field in _COMPARE_FIELDS},
            }
        )
    return {"fields": list(_COMPARE_FIELDS), "rows": rows}


# ---------------------------------------------------------------------------
# Export config (requirement D)
# ---------------------------------------------------------------------------
def export_config(
    *,
    preset_id: str,
    symbol: str = "XAUUSD",
    timeframe: Optional[str] = None,
    overrides: Optional[dict] = None,
    cost_scenario: str = "Base",
    custom_costs: Optional[dict] = None,
) -> dict:
    """Build a portable JSON strategy config for a later MT5 robot / bridge."""
    overrides = overrides or {}
    preset, merged, tf, cost_kwargs = prepare_run(
        preset_id=preset_id,
        symbol=symbol,
        timeframe=timeframe,
        overrides=overrides,
        cost_scenario=cost_scenario,
        custom_costs=custom_costs,
    )
    strategy_params, exit_params, risk_params = presets.split_parameters(preset, merged)

    notes = [
        presets.RESEARCH_DISCLAIMER,
        preset.recommended_use,
        *preset.warning_notes,
        *preset.extra_notes,
        presets.ML_RESEARCH_NOTE,
    ]

    return {
        "strategy_id": preset.preset_id,
        "version": CONFIG_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "timeframe": tf,
        "strategy_name": preset.strategy_name,
        "strategy_family": preset.family,
        "direction_mode": preset.direction_mode,
        "exit_mode": preset.exit_mode,
        "sizing_mode": preset.sizing_mode,
        "strategy_parameters": strategy_params,
        "exit_parameters": exit_params,
        "risk_parameters": risk_params,
        "stop_atr_period": presets.stop_atr_period(preset, merged),
        "cost_assumptions": {"scenario": cost_scenario, **cost_kwargs},
        "research_status": preset.research_status,
        "ml_filter_enabled": False,
        "notes": notes,
    }
