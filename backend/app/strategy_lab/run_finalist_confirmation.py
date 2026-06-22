"""Strategy Lab v1.4: deterministic finalist confirmation runner.

This runner is **research only** -- it never places, simulates or routes real
orders. Strategy Lab v1.2/v1.3 used *deterministic random sampling* of a huge
parameter space to surface a handful of promising XAUUSD long-only candidates.
This module takes those finalists and re-tests them **exhaustively and
deterministically**: every combination of a dense, finalist-local parameter grid
is run -- no random sampling, no seed dependency -- under several realistic cost
scenarios, leverage settings, and train/test/walk-forward splits.

The four shortlisted finalists (all long-only XAUUSD) are:

    A. H4 SuperTrend, ATR trailing exit, fixed-lot   -- high return.
    B. H4 SuperTrend, ATR trailing exit, fixed-lot   -- robust walk-forward.
    C. H1 Donchian breakout, fixed-ATR exit, risk-%  -- high return.
    D. H4 SuperTrend, ATR trailing exit, risk-%      -- low drawdown.

For every run the runner computes account-level metrics over the full period and
each split, derives stability diagnostics (walk-forward consistency, return
concentration, cost sensitivity), and ranks the survivors with a research-only
``confirmation_score``.

Reuse: signals (:mod:`strategies`), indicators (:mod:`indicators`), data loading
(:mod:`data_loader`), the leverage/margin-aware backtester
(:mod:`risk_backtester`) and account metrics (:mod:`metrics`) are all reused --
no backtesting logic is duplicated here.

Correctness / no-lookahead (inherited from :mod:`risk_backtester`):
    * a signal on bar ``i`` is entered at the **open of bar ``i + 1``**,
    * only completed-bar indicators are used,
    * trades are attributed to periods by their realised ``exit_time``.

Run it directly::

    python backend/app/strategy_lab/run_finalist_confirmation.py

or as a module from the ``backend`` directory::

    python -m app.strategy_lab.run_finalist_confirmation

Outputs land under ``MetaTrader_Data/reports/finalist_confirmation_v1_4/`` and
are *not* committed (the reports directory is git-ignored). Depends on
pandas/numpy only. No ML, no UI; original MT5 exports are never modified.
"""

from __future__ import annotations

import argparse
import itertools
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

import numpy as np
import pandas as pd

# Allow execution both as a script and as a package module.
try:
    from . import data_loader, indicators, metrics, risk_backtester, strategies
    from .risk_backtester import RiskConfig
except ImportError:  # running as a plain script
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import data_loader  # type: ignore[no-redef]
    import indicators  # type: ignore[no-redef]
    import metrics  # type: ignore[no-redef]
    import risk_backtester  # type: ignore[no-redef]
    import strategies  # type: ignore[no-redef]
    from risk_backtester import RiskConfig  # type: ignore[no-redef]

# Repo root: run_finalist_confirmation.py -> strategy_lab -> app -> backend -> ROOT
REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "MetaTrader_Data" / "mt5_exports"
OUTPUT_DIR = REPO_ROOT / "MetaTrader_Data" / "reports" / "finalist_confirmation_v1_4"

# Source file per timeframe (read-only; never modified).
TIMEFRAME_FILES: dict[str, str] = {
    "H1": "XAUUSDrfd_H1.csv",
    "H4": "XAUUSDrfd_H4.csv",
}

# ---------------------------------------------------------------------------
# Requirement 6: account / leverage assumptions (shared by every run).
# ---------------------------------------------------------------------------
# Leverage does NOT change PnL at a fixed lot size -- a 0.10-lot trade earns the
# same dollars at 10x or 50x. Leverage only changes how much *margin* a position
# ties up and therefore whether an entry is affordable (insufficient_margin) or
# gets force-liquidated (stop_out). The backtester models exactly this, so we run
# every leverage and let the margin model decide if it ever bites.
ACCOUNT_DEFAULTS = dict(
    account_currency="USD",
    contract_size=100.0,  # 1.0 lot XAUUSD controls 100 oz.
    point_value=0.01,  # gold: 1 point = 0.01 price units.
)
LEVERAGES: tuple[float, ...] = (10.0, 20.0, 50.0)

# Donchian (finalist C) has no ATR in its signal, but its fixed-ATR stop still
# needs an ATR period. We use 14 -- the value the v1.2/v1.3 research that surfaced
# this finalist ran with -- so the confirmation stays comparable.
DONCHIAN_ATR_PERIOD = 14

# ---------------------------------------------------------------------------
# Requirement 5: cost & execution scenarios.
# ---------------------------------------------------------------------------
# Each scenario is the full set of cost knobs handed to ``RiskConfig``. Slippage
# is per-side and worsens *both* fills (see ``risk_backtester``): a long buys a
# little higher and sells a little lower; the round-turn drag is twice the
# per-side slippage. Swap is charged per lot per day a position is held; only the
# long swap matters here because every finalist is long-only.
COST_SCENARIOS: dict[str, dict] = {
    "Base": dict(
        fixed_spread_points=30.0,
        slippage_points=0.0,
        commission_per_lot_round_turn=0.0,
        swap_long_per_lot_per_day=0.0,
        swap_short_per_lot_per_day=0.0,
    ),
    "Conservative": dict(
        fixed_spread_points=45.0,
        slippage_points=10.0,
        commission_per_lot_round_turn=7.0,
        swap_long_per_lot_per_day=-5.0,
        swap_short_per_lot_per_day=0.0,
    ),
    "Stress": dict(
        fixed_spread_points=60.0,
        slippage_points=20.0,
        commission_per_lot_round_turn=10.0,
        swap_long_per_lot_per_day=-10.0,
        swap_short_per_lot_per_day=0.0,
    ),
}
COST_SCENARIO_ORDER: tuple[str, ...] = ("Base", "Conservative", "Stress")

# ---------------------------------------------------------------------------
# Requirement 7: train / test / walk-forward splits.
# ---------------------------------------------------------------------------
# Times in the backtester outputs are naive UTC wall time, so the bounds below
# are naive too. Trades are attributed to a period by realised ``exit_time``; the
# per-bar equity curve is sliced by ``datetime`` for period-local drawdown.


@dataclass(frozen=True)
class Period:
    """One reporting window. ``start``/``end`` are inclusive (None == open)."""

    label: str
    start: Optional[pd.Timestamp]
    end: Optional[pd.Timestamp]
    kind: str  # 'full' | 'train' | 'test' | 'wf'


def _year_bounds(low: int, high: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Inclusive [Jan 1 of ``low`` .. Dec 31 23:59:59 of ``high``]."""
    return pd.Timestamp(f"{low}-01-01"), pd.Timestamp(f"{high}-12-31 23:59:59")


WALK_FORWARD_SPLITS: tuple[tuple[str, int, int], ...] = (
    ("wf_2015_2018", 2015, 2018),
    ("wf_2019_2021", 2019, 2021),
    ("wf_2022_2024", 2022, 2024),
    ("wf_2025_2026", 2025, 2026),
)

PERIODS: tuple[Period, ...] = (
    Period("full", None, None, "full"),
    Period("train", *_year_bounds(2015, 2021), "train"),
    # Test runs from 2022-01-01 to the last available bar (open-ended end).
    Period("test", pd.Timestamp("2022-01-01"), None, "test"),
    *[
        Period(label, *_year_bounds(lo, hi), "wf")
        for label, lo, hi in WALK_FORWARD_SPLITS
    ],
)
WALK_FORWARD_LABELS: tuple[str, ...] = tuple(
    label for label, _, _ in WALK_FORWARD_SPLITS
)


# ---------------------------------------------------------------------------
# Requirement 4: finalist definitions + dense local grids.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FinalistDef:
    """One shortlisted finalist and the dense grid to confirm it over.

    ``strategy_keys`` / ``exit_keys`` / ``sizing_keys`` partition the grid axes
    into the three ``RiskConfig`` concerns. ``config_atr_period_key`` names the
    grid axis that also drives the stop-ATR period (SuperTrend's ``atr_period``);
    finalists without one fall back to ``default_atr_period``.
    """

    key: str
    timeframe: str
    family: str  # 'supertrend' | 'donchian'
    direction: str
    exit_mode: str  # 'atr_trailing' | 'fixed_atr'
    sizing_mode: str  # 'fixed_lot' | 'risk_percent'
    strategy_keys: tuple[str, ...]
    exit_keys: tuple[str, ...]
    sizing_keys: tuple[str, ...]
    grid: dict[str, list]
    config_atr_period_key: Optional[str] = None
    default_atr_period: int = DONCHIAN_ATR_PERIOD


FINALISTS: dict[str, FinalistDef] = {
    # A. High-return fixed-lot SuperTrend (H4).
    "A": FinalistDef(
        key="A",
        timeframe="H4",
        family="supertrend",
        direction="long_only",
        exit_mode="atr_trailing",
        sizing_mode="fixed_lot",
        strategy_keys=("atr_period", "multiplier"),
        exit_keys=("initial_stop_loss_atr", "trailing_stop_atr", "take_profit_atr"),
        sizing_keys=("fixed_lot",),
        config_atr_period_key="atr_period",
        grid={
            "atr_period": [10, 14],
            "multiplier": [1.8, 2.0, 2.2, 2.5],
            "initial_stop_loss_atr": [4.0, 5.0, 6.0],
            "trailing_stop_atr": [3.0, 4.0, 5.0],
            "take_profit_atr": [None, 30.0],
            "fixed_lot": [0.05, 0.10, 0.15],
        },
    ),
    # B. Robust fixed-lot walk-forward SuperTrend (H4).
    "B": FinalistDef(
        key="B",
        timeframe="H4",
        family="supertrend",
        direction="long_only",
        exit_mode="atr_trailing",
        sizing_mode="fixed_lot",
        strategy_keys=("atr_period", "multiplier"),
        exit_keys=("initial_stop_loss_atr", "trailing_stop_atr", "take_profit_atr"),
        sizing_keys=("fixed_lot",),
        config_atr_period_key="atr_period",
        grid={
            "atr_period": [10, 14],
            "multiplier": [2.0, 2.5, 3.0],
            "initial_stop_loss_atr": [2.5, 3.0, 3.5, 4.0],
            "trailing_stop_atr": [3.0, 4.0, 5.0],
            "take_profit_atr": [20.0, 30.0, None],
            "fixed_lot": [0.05, 0.10, 0.15],
        },
    ),
    # C. High-return risk-percent Donchian breakout (H1).
    "C": FinalistDef(
        key="C",
        timeframe="H1",
        family="donchian",
        direction="long_only",
        exit_mode="fixed_atr",
        sizing_mode="risk_percent",
        strategy_keys=("lookback",),
        exit_keys=("stop_loss_atr", "take_profit_atr"),
        sizing_keys=("risk_percent",),
        config_atr_period_key=None,  # Donchian has no ATR signal -> default period.
        grid={
            "lookback": [40, 55, 70, 80],
            "stop_loss_atr": [2.5, 3.0, 3.5, 4.0],
            "take_profit_atr": [12.0, 16.0, 20.0, 24.0],
            "risk_percent": [0.5, 0.75, 1.0],
        },
    ),
    # D. Low-drawdown risk-percent SuperTrend (H4).
    "D": FinalistDef(
        key="D",
        timeframe="H4",
        family="supertrend",
        direction="long_only",
        exit_mode="atr_trailing",
        sizing_mode="risk_percent",
        strategy_keys=("atr_period", "multiplier"),
        exit_keys=("initial_stop_loss_atr", "trailing_stop_atr", "take_profit_atr"),
        sizing_keys=("risk_percent",),
        config_atr_period_key="atr_period",
        grid={
            "atr_period": [10, 14],
            "multiplier": [2.0, 2.5, 3.0],
            "initial_stop_loss_atr": [2.5, 3.0, 3.5, 4.0],
            "trailing_stop_atr": [5.0, 6.0, 7.0, 8.0],
            "take_profit_atr": [16.0, 20.0, 24.0, None],
            "risk_percent": [0.5, 0.75, 1.0],
        },
    ),
}

_SIGNAL_FUNCS: dict[str, Callable[..., pd.DataFrame]] = {
    "donchian": strategies.donchian_breakout_strategy,
    "supertrend": strategies.supertrend_strategy,
}


# ---------------------------------------------------------------------------
# Run specification + deterministic enumeration
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RunSpec:
    """Everything needed to materialise and identify one backtest run."""

    finalist: str
    timeframe: str
    family: str
    direction: str
    exit_mode: str
    sizing_mode: str
    config_atr_period: int
    signal_params: dict  # strategy params (drive the signal + cache key)
    exit_params: dict  # RiskConfig exit kwargs
    sizing_params: dict  # RiskConfig sizing kwargs
    cost_scenario: str
    leverage: float
    combo_id: str  # identity without leverage/scenario
    cost_group_id: str  # combo + leverage (shared by the 3 cost scenarios)
    config_id: str  # full identity (one run)


def _fmt(value: object) -> str:
    """Compact, stable string for a grid value (``None`` -> ``none``)."""
    if value is None:
        return "none"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _strategy_str(family: str, params: dict) -> str:
    if family == "supertrend":
        return f"st{_fmt(params['atr_period'])}_{_fmt(params['multiplier'])}"
    return f"dc{_fmt(params['lookback'])}"  # donchian


def _exit_str(exit_mode: str, params: dict) -> str:
    if exit_mode == "atr_trailing":
        return (
            f"trail_isl{_fmt(params['initial_stop_loss_atr'])}"
            f"_ts{_fmt(params['trailing_stop_atr'])}"
            f"_tp{_fmt(params['take_profit_atr'])}"
        )
    return (  # fixed_atr
        f"fixed_sl{_fmt(params['stop_loss_atr'])}_tp{_fmt(params['take_profit_atr'])}"
    )


def _sizing_str(sizing_mode: str, params: dict) -> str:
    if sizing_mode == "fixed_lot":
        return f"lot{_fmt(params['fixed_lot'])}"
    return f"risk{_fmt(params['risk_percent'])}"


def _split_combo(fin: FinalistDef, combo: dict) -> tuple[dict, dict, dict]:
    """Split one grid combination into (strategy, exit, sizing) param dicts."""
    strategy_params = {k: combo[k] for k in fin.strategy_keys}
    exit_params = {k: combo[k] for k in fin.exit_keys}
    sizing_params = {k: combo[k] for k in fin.sizing_keys}
    return strategy_params, exit_params, sizing_params


def _iter_combos(fin: FinalistDef) -> Iterable[dict]:
    """Yield every grid combination in a fixed (deterministic) axis order."""
    keys = list(fin.grid.keys())  # dict preserves insertion order -> deterministic
    for values in itertools.product(*(fin.grid[k] for k in keys)):
        yield dict(zip(keys, values))


def build_runs(
    finalists: Iterable[str],
    leverages: Iterable[float],
    cost_scenarios: Iterable[str],
) -> list[RunSpec]:
    """Enumerate all runs deterministically.

    Order is finalist -> grid combo -> leverage -> cost scenario, with the cost
    scenario innermost so the three scenarios of a given (combo, leverage) sit
    adjacent -- exactly the group used for cost-sensitivity and stress analysis.
    """
    runs: list[RunSpec] = []
    for fkey in finalists:
        fin = FINALISTS[fkey]
        for combo in _iter_combos(fin):
            strategy_params, exit_params, sizing_params = _split_combo(fin, combo)
            atr_period = (
                int(combo[fin.config_atr_period_key])
                if fin.config_atr_period_key
                else fin.default_atr_period
            )
            combo_id = (
                f"{fin.key}|{fin.timeframe}|"
                f"{_strategy_str(fin.family, strategy_params)}|"
                f"{_exit_str(fin.exit_mode, exit_params)}|"
                f"{_sizing_str(fin.sizing_mode, sizing_params)}"
            )
            for lev in leverages:
                cost_group_id = f"{combo_id}|lev{_fmt(lev)}"
                for scenario in cost_scenarios:
                    runs.append(
                        RunSpec(
                            finalist=fin.key,
                            timeframe=fin.timeframe,
                            family=fin.family,
                            direction=fin.direction,
                            exit_mode=fin.exit_mode,
                            sizing_mode=fin.sizing_mode,
                            config_atr_period=atr_period,
                            signal_params=strategy_params,
                            exit_params=exit_params,
                            sizing_params=sizing_params,
                            cost_scenario=scenario,
                            leverage=lev,
                            combo_id=combo_id,
                            cost_group_id=cost_group_id,
                            config_id=f"{cost_group_id}|{scenario}",
                        )
                    )
    return runs


def make_config(spec: RunSpec, initial_equity: float) -> RiskConfig:
    """Build the :class:`RiskConfig` for one run."""
    kwargs = dict(
        ACCOUNT_DEFAULTS,
        initial_equity=initial_equity,
        leverage=spec.leverage,
        atr_period=spec.config_atr_period,
        direction_mode=spec.direction,
        exit_mode=spec.exit_mode,
        sizing_mode=spec.sizing_mode,
    )
    kwargs.update(COST_SCENARIOS[spec.cost_scenario])
    kwargs.update(spec.exit_params)
    kwargs.update(spec.sizing_params)
    return RiskConfig(**kwargs)


def run_param_columns(spec: RunSpec) -> dict:
    """Flat identity + parameter columns for the summary tables."""
    sp, ep, zp = spec.signal_params, spec.exit_params, spec.sizing_params
    cost = COST_SCENARIOS[spec.cost_scenario]
    return {
        "config_id": spec.config_id,
        "cost_group_id": spec.cost_group_id,
        "combo_id": spec.combo_id,
        "finalist": spec.finalist,
        "timeframe": spec.timeframe,
        "strategy_family": spec.family,
        "direction": spec.direction,
        "exit_mode": spec.exit_mode,
        "sizing_mode": spec.sizing_mode,
        "atr_period": spec.config_atr_period,
        "multiplier": sp.get("multiplier"),
        "lookback": sp.get("lookback"),
        "initial_stop_loss_atr": ep.get("initial_stop_loss_atr"),
        "trailing_stop_atr": ep.get("trailing_stop_atr"),
        "stop_loss_atr": ep.get("stop_loss_atr"),
        "take_profit_atr": ep.get("take_profit_atr"),
        "fixed_lot": zp.get("fixed_lot"),
        "risk_percent": zp.get("risk_percent"),
        "cost_scenario": spec.cost_scenario,
        "fixed_spread_points": cost["fixed_spread_points"],
        "slippage_points": cost["slippage_points"],
        "commission_per_lot_round_turn": cost["commission_per_lot_round_turn"],
        "swap_long_per_lot_per_day": cost["swap_long_per_lot_per_day"],
        "leverage": spec.leverage,
    }


# ---------------------------------------------------------------------------
# Signal + ATR precomputation (reused across cost/leverage variants)
# ---------------------------------------------------------------------------
def precompute_market_data(
    runs: list[RunSpec],
) -> tuple[dict, dict, dict, Optional[str]]:
    """Load each needed timeframe once and cache signals + ATR.

    Signals depend only on (timeframe, family, strategy params); ATR depends only
    on (timeframe, atr_period). Cost scenario and leverage never touch either, so
    we compute them once and reuse them across the (large) run list.
    """
    timeframes = {spec.timeframe for spec in runs}
    df_cache: dict[str, pd.DataFrame] = {}
    atr_cache: dict[tuple[str, int], np.ndarray] = {}
    signal_cache: dict[tuple, pd.DataFrame] = {}
    symbol: Optional[str] = None

    for tf in sorted(timeframes):
        path = DATA_DIR / TIMEFRAME_FILES[tf]
        print(f"Loading {path} ...")
        df = data_loader.load_mt5_csv(path)
        print(f"  {len(df)} bars ({df['datetime'].iloc[0]} -> {df['datetime'].iloc[-1]})")
        df_cache[tf] = df
        if symbol is None and len(df):
            symbol = df["symbol"].iloc[0]

    for spec in runs:
        atr_key = (spec.timeframe, spec.config_atr_period)
        if atr_key not in atr_cache:
            atr_cache[atr_key] = (
                indicators.atr(df_cache[spec.timeframe], spec.config_atr_period)
                .to_numpy(dtype=float)
            )
        sig_key = _signal_cache_key(spec)
        if sig_key not in signal_cache:
            func = _SIGNAL_FUNCS[spec.family]
            signal_cache[sig_key] = func(df_cache[spec.timeframe], **spec.signal_params)

    print(
        f"Precomputed {len(signal_cache)} signal sets and "
        f"{len(atr_cache)} ATR series across {len(df_cache)} timeframe(s)."
    )
    return df_cache, atr_cache, signal_cache, symbol


def _signal_cache_key(spec: RunSpec) -> tuple:
    """Hashable key identifying a unique signal set."""
    return (spec.timeframe, spec.family, tuple(sorted(spec.signal_params.items())))


# ---------------------------------------------------------------------------
# Requirement 8: per-period metrics (reuses metrics.compute_risk_metrics)
# ---------------------------------------------------------------------------
def _slice_by_time(
    df: pd.DataFrame, time_col: str, start: Optional[pd.Timestamp], end: Optional[pd.Timestamp]
) -> pd.DataFrame:
    """Inclusive [start, end] slice on ``time_col`` (None == open-ended)."""
    if start is None and end is None:
        return df
    times = df[time_col]
    mask = np.ones(len(df), dtype=bool)
    if start is not None:
        mask &= (times >= start).to_numpy()
    if end is not None:
        mask &= (times <= end).to_numpy()
    return df.loc[mask]


def _period_local_drawdown(ec_slice: pd.DataFrame) -> pd.DataFrame:
    """Recompute drawdown columns relative to the slice's *own* running peak.

    The full-curve ``drawdown``/``drawdown_pct`` measure depth below the all-time
    high; for a period we want depth below that period's own peak, so we reset the
    peak to the start of the slice.
    """
    out = ec_slice.copy()
    equity = out["equity"].astype(float)
    peak = equity.cummax()
    out["drawdown"] = peak - equity
    out["drawdown_pct"] = np.where(peak > 0, (peak - equity) / peak * 100.0, 0.0)
    return out


def _effective_leverage(trades_sub: pd.DataFrame) -> tuple[float, float]:
    """(average, max) exposure-to-equity at entry over a trade subset.

    Exposure is the entry notional; equity is the account equity *before* the
    trade (one position at a time, so ``balance_after_trade - net_pnl``).
    """
    if trades_sub is None or len(trades_sub) == 0:
        return (np.nan, np.nan)
    notional = trades_sub["notional"].astype(float).to_numpy()
    equity_before = (
        trades_sub["balance_after_trade"].astype(float)
        - trades_sub["net_pnl"].astype(float)
    ).to_numpy()
    eff = np.full(len(notional), np.nan)
    ok = equity_before > 0
    eff[ok] = notional[ok] / equity_before[ok]
    if not np.any(np.isfinite(eff)):
        return (np.nan, np.nan)
    return (float(np.nanmean(eff)), float(np.nanmax(eff)))


# The exact per-period metric set requested by requirement 8.
PERIOD_METRIC_KEYS: tuple[str, ...] = (
    "initial_equity",
    "final_equity",
    "total_return_pct",
    "net_profit",
    "total_trades",
    "winning_trades",
    "losing_trades",
    "win_rate",
    "profit_factor",
    "average_trade",
    "median_trade",
    "max_drawdown",
    "max_drawdown_pct",
    "max_equity",
    "min_equity",
    "max_margin_used",
    "min_margin_level",
    "stop_out_count",
    "insufficient_margin_count",
    "average_lots",
    "max_lots",
    "average_effective_leverage",
    "max_effective_leverage",
    "average_r",
    "median_r",
    "max_consecutive_losses",
    "long_trades",
    "long_net_profit",
)


def compute_period_metrics(
    trades_sub: pd.DataFrame,
    ec_slice: pd.DataFrame,
    skipped_sub: pd.DataFrame,
    *,
    period_start_equity: float,
) -> dict:
    """Full requirement-8 metric set for one period.

    Reuses :func:`metrics.compute_risk_metrics` for the bulk of the work (counts,
    profit factor, drawdown, margin, R, long stats) and only adds the two
    effective-leverage figures it does not provide. The equity slice carries
    period-local drawdown so the figures describe risk taken *within* the period.
    """
    ec_local = (
        _period_local_drawdown(ec_slice) if ec_slice is not None and len(ec_slice) else ec_slice
    )
    base = metrics.compute_risk_metrics(
        trades_sub, ec_local, skipped_sub, initial_equity=period_start_equity
    )
    avg_lev, max_lev = _effective_leverage(trades_sub)
    base["average_effective_leverage"] = avg_lev
    base["max_effective_leverage"] = max_lev
    return {k: base[k] for k in PERIOD_METRIC_KEYS}


def evaluate_run_periods(
    trades: pd.DataFrame,
    equity_curve: pd.DataFrame,
    skipped: pd.DataFrame,
    *,
    initial_equity: float,
) -> dict[str, dict]:
    """Compute metrics for every reporting period of a single run."""
    results: dict[str, dict] = {}
    for period in PERIODS:
        ec_slice = _slice_by_time(equity_curve, "datetime", period.start, period.end)
        trades_sub = _slice_by_time(trades, "exit_time", period.start, period.end)
        skipped_sub = (
            _slice_by_time(skipped, "entry_time", period.start, period.end)
            if len(skipped)
            else skipped
        )
        # The period's opening equity = mark-to-market equity at its first bar
        # (== the account's starting equity for the full period).
        start_equity = (
            float(ec_slice["equity"].iloc[0]) if len(ec_slice) else float(initial_equity)
        )
        results[period.label] = compute_period_metrics(
            trades_sub, ec_slice, skipped_sub, period_start_equity=start_equity
        )
    return results


# ---------------------------------------------------------------------------
# Requirement 9: stability diagnostics (per run, from its period metrics)
# ---------------------------------------------------------------------------
def _ratio(numerator: float, denominator: float) -> float:
    """``numerator / denominator`` with a NaN guard on a ~zero denominator."""
    if denominator is None or not np.isfinite(denominator) or abs(denominator) < 1e-9:
        return float("nan")
    return numerator / denominator


def stability_diagnostics(periods_m: dict[str, dict]) -> dict:
    """Walk-forward consistency, train/test and concentration diagnostics.

    ``cost_sensitivity_score`` is *not* set here -- it compares different cost
    scenarios and is filled in once the whole sweep is available.
    """
    wf = [periods_m[label] for label in WALK_FORWARD_LABELS]
    wf_net = [m["net_profit"] for m in wf]
    wf_ret = [m["total_return_pct"] for m in wf]
    wf_dd = [m["max_drawdown_pct"] for m in wf]

    train, test, full = periods_m["train"], periods_m["test"], periods_m["full"]
    full_net = full["net_profit"]
    best_wf_net = max(wf_net) if wf_net else float("nan")

    return {
        "profitable_walk_forward_periods": int(sum(1 for v in wf_net if v > 0)),
        "losing_walk_forward_periods": int(sum(1 for v in wf_net if v < 0)),
        "worst_walk_forward_return_pct": float(min(wf_ret)) if wf_ret else float("nan"),
        "worst_walk_forward_drawdown_pct": float(max(wf_dd)) if wf_dd else float("nan"),
        "train_return_pct": float(train["total_return_pct"]),
        "test_return_pct": float(test["total_return_pct"]),
        "train_profit_factor": float(train["profit_factor"]),
        "test_profit_factor": float(test["profit_factor"]),
        "train_to_test_return_ratio": _ratio(
            train["total_return_pct"], test["total_return_pct"]
        ),
        # Did one walk-forward period carry the whole result? Only meaningful when
        # the strategy is net profitable overall.
        "return_concentration_ratio": (
            _ratio(best_wf_net, full_net) if full_net > 0 else float("nan")
        ),
    }


# ---------------------------------------------------------------------------
# Main sweep
# ---------------------------------------------------------------------------
def run_sweep(
    runs: list[RunSpec],
    *,
    df_cache: dict,
    atr_cache: dict,
    signal_cache: dict,
    symbol: Optional[str],
    initial_equity: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Execute every run; return (summary_full, period_summary) frames.

    ``summary_full`` has one row per run (full-period metrics + stability);
    ``period_summary`` has one row per (run, period).
    """
    summary_rows: list[dict] = []
    period_rows: list[dict] = []
    total = len(runs)

    for n, spec in enumerate(runs, start=1):
        config = make_config(spec, initial_equity)
        trades, equity_curve, skipped = risk_backtester.run_risk_backtest(
            df_cache[spec.timeframe],
            signal_cache[_signal_cache_key(spec)],
            config,
            strategy_name=spec.combo_id,
            symbol=symbol,
            timeframe=spec.timeframe,
            atr_values=atr_cache[(spec.timeframe, spec.config_atr_period)],
        )

        periods_m = evaluate_run_periods(
            trades, equity_curve, skipped, initial_equity=initial_equity
        )
        params = run_param_columns(spec)

        # One row per (run, period) for the long-format period tables.
        for label, m in periods_m.items():
            period_rows.append(
                {
                    "config_id": spec.config_id,
                    "finalist": spec.finalist,
                    "cost_scenario": spec.cost_scenario,
                    "leverage": spec.leverage,
                    "period": label,
                    **m,
                }
            )

        # One summary row: identity + full-period metrics + stability diagnostics.
        summary_rows.append(
            {**params, **periods_m["full"], **stability_diagnostics(periods_m)}
        )

        if n % 250 == 0 or n == total:
            print(f"  ... {n}/{total} runs")

    summary_df = pd.DataFrame(summary_rows)
    period_df = pd.DataFrame(period_rows)
    return summary_df, period_df


# ---------------------------------------------------------------------------
# Requirement 9 (cost sensitivity) + 13 (confirmation score)
# ---------------------------------------------------------------------------
def add_cost_sensitivity(summary: pd.DataFrame) -> pd.DataFrame:
    """Attach ``cost_sensitivity_score`` to every run from its cost group.

    For each (combo, leverage) group we compare the full-period return of the
    Base scenario with the worst of {Conservative, Stress}. The score is the
    relative collapse: 0 == costs barely dent it, 1 == returns wiped out, >1 ==
    turned a profit into a loss. Groups lacking all three scenarios (e.g. a
    ``--cost-scenario`` filtered run) get NaN, since sensitivity is undefined.
    """
    summary = summary.copy()
    summary["cost_sensitivity_score"] = np.nan
    returns = summary.set_index(["cost_group_id", "cost_scenario"])["total_return_pct"]

    scores: dict[str, float] = {}
    for group_id, grp in summary.groupby("cost_group_id", sort=False):
        present = set(grp["cost_scenario"])
        if not {"Base", "Conservative", "Stress"} <= present:
            continue
        base = float(returns.loc[(group_id, "Base")])
        worst = min(
            float(returns.loc[(group_id, "Conservative")]),
            float(returns.loc[(group_id, "Stress")]),
        )
        if base > 0:
            scores[group_id] = max(0.0, (base - worst) / base)
        else:
            # Not even profitable before costs -> maximally "sensitive".
            scores[group_id] = 1.0

    summary["cost_sensitivity_score"] = summary["cost_group_id"].map(scores)
    return summary


def add_confirmation_score(summary: pd.DataFrame) -> pd.DataFrame:
    """Attach the research-only ``confirmation_score`` (requirement 13).

    ``profit_factor`` can be +inf (no losing trades); it is clipped purely so the
    score sorts sensibly. Undefined concentration / cost-sensitivity (NaN) are
    treated as neutral 0 for the score only. This score ranks research candidates
    -- it is NOT a trading decision or a performance guarantee.
    """
    summary = summary.copy()
    pf = summary["profit_factor"].replace([np.inf, -np.inf], 100.0).clip(upper=100.0)
    concentration = summary["return_concentration_ratio"].fillna(0.0)
    cost_sens = summary["cost_sensitivity_score"].fillna(0.0)

    summary["confirmation_score"] = (
        summary["total_return_pct"]
        + pf * 25.0
        - summary["max_drawdown_pct"] * 1.5
        + summary["profitable_walk_forward_periods"] * 10.0
        + summary["test_return_pct"] * 0.5
        - concentration * 20.0
        - cost_sens * 10.0
    )
    return summary


# ---------------------------------------------------------------------------
# Requirement 11: shortlists + rejection reasons
# ---------------------------------------------------------------------------
def select_top_finalists(summary: pd.DataFrame, initial_equity: float) -> pd.DataFrame:
    """Runs passing every confirmation gate, ranked by confirmation_score."""
    mask = top_finalist_mask(summary, initial_equity)
    return (
        summary[mask]
        .sort_values("confirmation_score", ascending=False)
        .reset_index(drop=True)
    )


def top_finalist_mask(summary: pd.DataFrame, initial_equity: float) -> pd.Series:
    """Boolean mask for the requirement-11 top-finalist filters."""
    return (
        (summary["total_trades"] >= 50)
        & (summary["final_equity"] > initial_equity)
        & (summary["profit_factor"] >= 1.25)
        & (summary["max_drawdown_pct"] <= 30.0)
        & (summary["stop_out_count"] == 0)
        & (summary["insufficient_margin_count"] == 0)
        & (summary["profitable_walk_forward_periods"] >= 3)
        & (summary["test_return_pct"] > 0)
        & (summary["test_profit_factor"] >= 1.1)
    )


def select_low_drawdown(summary: pd.DataFrame) -> pd.DataFrame:
    """Shallow-drawdown, high-PF survivors (requirement 11)."""
    mask = (
        (summary["max_drawdown_pct"] <= 15.0)
        & (summary["profit_factor"] >= 1.4)
        & (summary["total_trades"] >= 50)
        & (summary["stop_out_count"] == 0)
        & (summary["test_return_pct"] > 0)
    )
    return (
        summary[mask]
        .sort_values(
            ["max_drawdown_pct", "confirmation_score"], ascending=[True, False]
        )
        .reset_index(drop=True)
    )


# Stress-resistance metrics columns produced per cost group.
_STRESS_COLUMNS: tuple[str, ...] = (
    "cost_group_id",
    "finalist",
    "combo_id",
    "leverage",
    "base_return_pct",
    "conservative_return_pct",
    "stress_return_pct",
    "stress_max_drawdown_pct",
    "stress_profit_factor",
    "worst_case_return_pct",
    "stress_resistance_score",
    "cost_sensitivity_score",
    "stress_resistant",
)


def cost_group_table(summary: pd.DataFrame) -> pd.DataFrame:
    """One row per (combo, leverage) group with Base/Conservative/Stress facts.

    Stress resistance (requirement 11) needs all three scenarios of a config in
    one place, so we pivot the per-run summary onto its cost group here. The same
    table feeds ``finalist_sensitivity_summary.csv``.
    """
    needed = (
        "cost_group_id",
        "finalist",
        "combo_id",
        "leverage",
        "cost_scenario",
        "total_return_pct",
        "max_drawdown_pct",
        "profit_factor",
        "stop_out_count",
        "insufficient_margin_count",
        "cost_sensitivity_score",
        "confirmation_score",
    )
    rows: list[dict] = []
    for group_id, grp in summary[list(needed)].groupby("cost_group_id", sort=False):
        by_scn = grp.set_index("cost_scenario")
        if not {"Base", "Conservative", "Stress"} <= set(by_scn.index):
            continue  # incomplete group (filtered run) -> cannot judge stress
        base, cons, stress = (
            by_scn.loc["Base"],
            by_scn.loc["Conservative"],
            by_scn.loc["Stress"],
        )
        returns = [
            float(base["total_return_pct"]),
            float(cons["total_return_pct"]),
            float(stress["total_return_pct"]),
        ]
        all_profitable = all(r > 0 for r in returns)
        no_stop_out = (
            base["stop_out_count"]
            + cons["stop_out_count"]
            + stress["stop_out_count"]
        ) == 0
        no_insufficient = (
            base["insufficient_margin_count"]
            + cons["insufficient_margin_count"]
            + stress["insufficient_margin_count"]
        ) == 0
        stress_resistant = bool(
            all_profitable
            and float(stress["max_drawdown_pct"]) <= 35.0
            and float(stress["profit_factor"]) >= 1.1
            and no_stop_out
            and no_insufficient
        )
        rows.append(
            {
                "cost_group_id": group_id,
                "finalist": grp["finalist"].iloc[0],
                "combo_id": grp["combo_id"].iloc[0],
                "leverage": grp["leverage"].iloc[0],
                "base_return_pct": returns[0],
                "conservative_return_pct": returns[1],
                "stress_return_pct": returns[2],
                "stress_max_drawdown_pct": float(stress["max_drawdown_pct"]),
                "stress_profit_factor": float(stress["profit_factor"]),
                # Worst-case profitability across scenarios -> the headline rank.
                "worst_case_return_pct": min(returns),
                "stress_resistance_score": min(returns)
                + float(stress["profit_factor"]) * 25.0
                - float(stress["max_drawdown_pct"]) * 1.5,
                "cost_sensitivity_score": float(base["cost_sensitivity_score"]),
                "stress_resistant": stress_resistant,
            }
        )
    return pd.DataFrame(rows, columns=list(_STRESS_COLUMNS))


def select_stress_resistant(cost_groups: pd.DataFrame) -> pd.DataFrame:
    """Stress-resistant config groups, ranked by worst-case robustness."""
    if cost_groups.empty:
        return cost_groups
    return (
        cost_groups[cost_groups["stress_resistant"]]
        .sort_values("stress_resistance_score", ascending=False)
        .reset_index(drop=True)
    )


# Requirement 11: the rejection-reason vocabulary, paired with its test.
_REJECTION_RULES: tuple[tuple[str, Callable[[pd.Series, float], bool]], ...] = (
    ("too_few_trades", lambda r, eq: r["total_trades"] < 50),
    ("unprofitable", lambda r, eq: r["final_equity"] <= eq),
    ("weak_profit_factor", lambda r, eq: r["profit_factor"] < 1.25),
    ("excessive_drawdown", lambda r, eq: r["max_drawdown_pct"] > 30.0),
    ("stop_out", lambda r, eq: r["stop_out_count"] > 0),
    ("insufficient_margin", lambda r, eq: r["insufficient_margin_count"] > 0),
    ("weak_walk_forward", lambda r, eq: r["profitable_walk_forward_periods"] < 3),
    ("negative_test_period", lambda r, eq: r["test_return_pct"] <= 0),
    ("weak_test_profit_factor", lambda r, eq: r["test_profit_factor"] < 1.1),
)


def build_rejected(
    summary: pd.DataFrame, cost_groups: pd.DataFrame, initial_equity: float
) -> pd.DataFrame:
    """Every run rejected from top_finalists, with its rejection reason(s).

    ``poor_stress_resistance`` is appended for runs whose cost group is not
    stress-resistant -- it is not itself a top-finalist gate, but the spec lists
    it among the diagnostic reasons worth surfacing.
    """
    rejected_mask = ~top_finalist_mask(summary, initial_equity)
    rejected = summary[rejected_mask].copy()
    if rejected.empty:
        rejected["rejection_reasons"] = []
        return rejected

    not_resistant = set(
        cost_groups.loc[~cost_groups["stress_resistant"], "cost_group_id"]
    )

    def reasons_for(row: pd.Series) -> str:
        reasons = [
            name for name, test in _REJECTION_RULES if test(row, initial_equity)
        ]
        if row["cost_group_id"] in not_resistant:
            reasons.append("poor_stress_resistance")
        return ";".join(reasons)

    rejected["rejection_reasons"] = rejected.apply(reasons_for, axis=1)
    return rejected.sort_values("confirmation_score", ascending=False).reset_index(
        drop=True
    )


# ---------------------------------------------------------------------------
# Requirement 10: derived period tables
# ---------------------------------------------------------------------------
def build_walk_forward_summary(period_summary: pd.DataFrame) -> pd.DataFrame:
    """Long-format walk-forward table (the WF periods of every run)."""
    return period_summary[
        period_summary["period"].isin(WALK_FORWARD_LABELS)
    ].reset_index(drop=True)


def build_train_test_summary(
    period_summary: pd.DataFrame, summary: pd.DataFrame
) -> pd.DataFrame:
    """Wide per-run train-vs-test table with the train/test return ratio."""
    keep = [
        "total_return_pct",
        "net_profit",
        "profit_factor",
        "max_drawdown_pct",
        "total_trades",
        "win_rate",
    ]
    train = period_summary[period_summary["period"] == "train"][["config_id", *keep]]
    test = period_summary[period_summary["period"] == "test"][["config_id", *keep]]
    train = train.rename(columns={c: f"train_{c}" for c in keep})
    test = test.rename(columns={c: f"test_{c}" for c in keep})

    id_cols = [
        "config_id",
        "finalist",
        "cost_scenario",
        "leverage",
        "train_to_test_return_ratio",
        "confirmation_score",
    ]
    out = summary[id_cols].merge(train, on="config_id", how="left")
    out = out.merge(test, on="config_id", how="left")
    return out.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Requirement 10: finalist trades (second pass over the ranked survivors)
# ---------------------------------------------------------------------------
def collect_finalist_trades(
    summary: pd.DataFrame,
    selections: list[pd.DataFrame],
    run_by_id: dict[str, RunSpec],
    *,
    df_cache: dict,
    atr_cache: dict,
    signal_cache: dict,
    symbol: Optional[str],
    initial_equity: float,
    limit: int,
) -> pd.DataFrame:
    """Re-run the top-ranked configs to materialise their executed trades.

    Writing a trade log for *all* runs would be multi-gigabyte, so -- like the
    v1.2 runner does for equity curves -- we dump trades only for the finalists
    that actually matter: the union of every shortlist, topped up by the best
    overall ``confirmation_score``, capped at ``limit`` configs.
    """
    chosen: list[str] = []
    for frame in selections:
        if frame is not None and not frame.empty and "config_id" in frame.columns:
            chosen.extend(frame["config_id"].tolist())
    # Top up / fall back to the best by confirmation_score.
    if not summary.empty:
        ranked = summary.sort_values("confirmation_score", ascending=False)
        chosen.extend(ranked["config_id"].tolist())

    seen: list[str] = []
    for cid in chosen:  # preserve order, de-duplicate, cap
        if cid not in seen and cid in run_by_id:
            seen.append(cid)
        if len(seen) >= limit:
            break

    frames: list[pd.DataFrame] = []
    for cid in seen:
        spec = run_by_id[cid]
        config = make_config(spec, initial_equity)
        trades, _ec, _sk = risk_backtester.run_risk_backtest(
            df_cache[spec.timeframe],
            signal_cache[_signal_cache_key(spec)],
            config,
            strategy_name=spec.combo_id,
            symbol=symbol,
            timeframe=spec.timeframe,
            atr_values=atr_cache[(spec.timeframe, spec.config_atr_period)],
        )
        if len(trades):
            trades = trades.copy()
            trades.insert(0, "config_id", cid)
            trades.insert(1, "finalist", spec.finalist)
            trades.insert(2, "cost_scenario", spec.cost_scenario)
            frames.append(trades)

    if frames:
        return pd.concat(frames, ignore_index=True)
    return pd.DataFrame(columns=["config_id", "finalist", "cost_scenario"])


# ---------------------------------------------------------------------------
# Requirement 12: console report
# ---------------------------------------------------------------------------
_SUMMARY_DISPLAY = [
    "config_id",
    "total_trades",
    "profit_factor",
    "total_return_pct",
    "max_drawdown_pct",
    "test_return_pct",
    "profitable_walk_forward_periods",
    "cost_sensitivity_score",
    "confirmation_score",
]
_STRESS_DISPLAY = [
    "cost_group_id",
    "base_return_pct",
    "conservative_return_pct",
    "stress_return_pct",
    "stress_max_drawdown_pct",
    "stress_profit_factor",
    "worst_case_return_pct",
    "stress_resistance_score",
]


def _print_table(df: pd.DataFrame, title: str, columns: list[str], n: int = 20) -> None:
    print(f"\n=== {title} (top {n}) ===")
    if df is None or df.empty:
        print("(none)")
        return
    cols = [c for c in columns if c in df.columns]
    with pd.option_context("display.max_columns", None, "display.width", 260):
        print(df.head(n)[cols].to_string(index=False))


def print_report(
    *,
    summary: pd.DataFrame,
    top_finalists: pd.DataFrame,
    low_drawdown: pd.DataFrame,
    stress_resistant: pd.DataFrame,
    output_dir: Path,
    total_runs: int,
) -> None:
    """End-of-run console summary (requirement 12)."""
    print("\n" + "=" * 72)
    print(f"Total runs executed:            {total_runs}")
    print(f"Output folder:                  {output_dir}")
    print(f"Passing top finalists:          {len(top_finalists)}")
    print(f"Low-drawdown finalists:         {len(low_drawdown)}")
    print(f"Stress-resistant config groups: {len(stress_resistant)}")

    _print_table(top_finalists, "Top finalists by confirmation_score", _SUMMARY_DISPLAY)
    _print_table(stress_resistant, "Top by stress resistance", _STRESS_DISPLAY)
    low_sorted = (
        summary[summary["total_trades"] >= 50].sort_values("max_drawdown_pct")
        if low_drawdown.empty
        else low_drawdown
    )
    _print_table(low_sorted, "Top low-drawdown candidates", _SUMMARY_DISPLAY)

    print("\n=== Cost scenarios ===")
    print(
        "Base         : spread 30pts, no slippage/commission/swap -- an idealised\n"
        "               broker; the optimistic upper bound.\n"
        "Conservative : spread 45pts, 10pt slippage, $7/lot commission, -$5/lot/day\n"
        "               long swap -- a realistic retail XAUUSD account.\n"
        "Stress       : spread 60pts, 20pt slippage, $10/lot commission, -$10/lot/day\n"
        "               long swap -- a punitive worst case for execution robustness.\n"
        "Slippage worsens both fills; only long swap is charged (all finalists are\n"
        "long-only). cost_sensitivity_score measures how far returns collapse from\n"
        "Base to the worst of Conservative/Stress (0 = robust, >=1 = wiped out)."
    )
    print("\n(Research only -- confirmation_score ranks candidates, it is not advice.)")


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
# Front-of-table column order for the master summary (rest follow as-is).
_SUMMARY_FRONT = [
    "config_id",
    "finalist",
    "timeframe",
    "strategy_family",
    "cost_scenario",
    "leverage",
    "atr_period",
    "multiplier",
    "lookback",
    "initial_stop_loss_atr",
    "trailing_stop_atr",
    "stop_loss_atr",
    "take_profit_atr",
    "fixed_lot",
    "risk_percent",
    "total_trades",
    "profit_factor",
    "total_return_pct",
    "max_drawdown_pct",
    "test_return_pct",
    "test_profit_factor",
    "profitable_walk_forward_periods",
    "return_concentration_ratio",
    "cost_sensitivity_score",
    "confirmation_score",
]


def _order_columns(df: pd.DataFrame, front: list[str]) -> pd.DataFrame:
    lead = [c for c in front if c in df.columns]
    rest = [c for c in df.columns if c not in lead]
    return df[lead + rest]


def write_outputs(
    out_dir: Path,
    *,
    trades: pd.DataFrame,
    summary: pd.DataFrame,
    period_summary: pd.DataFrame,
    walk_forward: pd.DataFrame,
    train_test: pd.DataFrame,
    sensitivity: pd.DataFrame,
    top_finalists: pd.DataFrame,
    low_drawdown: pd.DataFrame,
    stress_resistant: pd.DataFrame,
    rejected: pd.DataFrame,
) -> None:
    """Write every requirement-10 CSV (output directory is git-ignored)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = _order_columns(summary, _SUMMARY_FRONT)
    top_finalists = _order_columns(top_finalists, _SUMMARY_FRONT)
    low_drawdown = _order_columns(low_drawdown, _SUMMARY_FRONT)
    rejected = _order_columns(rejected, _SUMMARY_FRONT + ["rejection_reasons"])

    outputs: list[tuple[str, pd.DataFrame]] = [
        ("finalist_trades.csv", trades),
        ("finalist_summary_full.csv", summary),
        ("finalist_period_summary.csv", period_summary),
        ("finalist_walk_forward_summary.csv", walk_forward),
        ("finalist_train_test_summary.csv", train_test),
        ("finalist_sensitivity_summary.csv", sensitivity),
        ("top_finalists.csv", top_finalists),
        ("top_low_drawdown_finalists.csv", low_drawdown),
        ("top_stress_resistant_finalists.csv", stress_resistant),
        ("rejected_finalists.csv", rejected),
    ]
    print()
    for name, frame in outputs:
        path = out_dir / name
        frame.to_csv(path, index=False)
        print(f"Wrote {len(frame):>7} rows -> {path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Strategy Lab v1.4 deterministic finalist confirmation runner"
    )
    parser.add_argument(
        "--finalist",
        choices=sorted(FINALISTS.keys()),
        default=None,
        help="Restrict to a single finalist (A/B/C/D). Default: all.",
    )
    parser.add_argument(
        "--cost-scenario",
        choices=list(COST_SCENARIO_ORDER),
        default=None,
        help="Restrict to one cost scenario. Default: all three.",
    )
    parser.add_argument(
        "--leverage",
        type=float,
        choices=list(LEVERAGES),
        default=None,
        help="Restrict the leverage axis to one value. Default: all.",
    )
    parser.add_argument("--initial-equity", type=float, default=10000.0)
    parser.add_argument(
        "--max-runs",
        type=int,
        default=None,
        help="Safety cap on the number of runs (deterministic truncation).",
    )
    parser.add_argument(
        "--trades-limit",
        type=int,
        default=50,
        help="How many top configs to dump trades for in finalist_trades.csv.",
    )
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)

    finalists = [args.finalist] if args.finalist else sorted(FINALISTS.keys())
    leverages = [args.leverage] if args.leverage else list(LEVERAGES)
    scenarios = [args.cost_scenario] if args.cost_scenario else list(COST_SCENARIO_ORDER)

    runs = build_runs(finalists, leverages, scenarios)
    print(f"Enumerated {len(runs)} deterministic runs across finalists {finalists}.")
    if args.max_runs is not None and len(runs) > args.max_runs:
        runs = runs[: args.max_runs]
        print(f"Capped to first {len(runs)} runs (--max-runs={args.max_runs}).")
    run_by_id = {spec.config_id: spec for spec in runs}

    df_cache, atr_cache, signal_cache, symbol = precompute_market_data(runs)

    print(f"Executing {len(runs)} backtests ...")
    summary, period_summary = run_sweep(
        runs,
        df_cache=df_cache,
        atr_cache=atr_cache,
        signal_cache=signal_cache,
        symbol=symbol,
        initial_equity=args.initial_equity,
    )

    # Cross-scenario diagnostics, then the per-run ranking score.
    summary = add_cost_sensitivity(summary)
    summary = add_confirmation_score(summary)
    # Carry cost_sensitivity_score onto every (run, period) row for convenience.
    period_summary = period_summary.merge(
        summary[["config_id", "cost_sensitivity_score", "confirmation_score"]],
        on="config_id",
        how="left",
    )

    cost_groups = cost_group_table(summary)
    top_finalists = select_top_finalists(summary, args.initial_equity)
    low_drawdown = select_low_drawdown(summary)
    stress_resistant = select_stress_resistant(cost_groups)
    rejected = build_rejected(summary, cost_groups, args.initial_equity)

    walk_forward = build_walk_forward_summary(period_summary)
    train_test = build_train_test_summary(period_summary, summary)

    finalist_trades = collect_finalist_trades(
        summary,
        [top_finalists, low_drawdown],
        run_by_id,
        df_cache=df_cache,
        atr_cache=atr_cache,
        signal_cache=signal_cache,
        symbol=symbol,
        initial_equity=args.initial_equity,
        limit=args.trades_limit,
    )

    write_outputs(
        args.output_dir,
        trades=finalist_trades,
        summary=summary,
        period_summary=period_summary,
        walk_forward=walk_forward,
        train_test=train_test,
        sensitivity=cost_groups,
        top_finalists=top_finalists,
        low_drawdown=low_drawdown,
        stress_resistant=stress_resistant,
        rejected=rejected,
    )

    print_report(
        summary=summary,
        top_finalists=top_finalists,
        low_drawdown=low_drawdown,
        stress_resistant=stress_resistant,
        output_dir=args.output_dir,
        total_runs=len(runs),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
