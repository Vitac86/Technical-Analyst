"""Strategy Lab v1.3: robustness analysis & leverage diagnostics.

This runner is **research only** -- it never trades. It does *not* re-run any
backtests; instead it reads the v1.2 outputs already written by
:mod:`app.strategy_lab.run_risk_backtests` and turns them into robustness and
leverage diagnostics:

    * effective leverage (actual position exposure vs. account equity), which
      separates real exposure from the broker's nominal leverage,
    * a leverage comparison that proves whether broker leverage actually moved
      PnL for otherwise-identical configs (it only can via margin gating /
      stop-out, never via lot size),
    * yearly and fixed-split walk-forward robustness breakdowns,
    * return-concentration diagnostics (did one exceptional year carry it?),
    * separate candidate rankings per sizing mode plus low-drawdown and
      walk-forward shortlists.

Run it directly::

    python backend/app/strategy_lab/run_robustness_diagnostics.py

or as a module from the ``backend`` directory::

    python -m app.strategy_lab.run_robustness_diagnostics

Inputs (under ``MetaTrader_Data/reports/risk_backtests_v1_2/``):
    * ``summary.csv``         - one row of metrics per v1.2 run.
    * ``trades.csv``          - every executed trade across all runs.
    * ``skipped_trades.csv``  - entries skipped (e.g. insufficient_margin).

Outputs (under ``MetaTrader_Data/reports/risk_backtests_v1_3/``):
    * ``effective_leverage_summary.csv``
    * ``leverage_comparison.csv``
    * ``yearly_summary.csv``
    * ``walk_forward_summary.csv``
    * ``concentration_summary.csv``
    * ``top_fixed_lot_candidates.csv``
    * ``top_risk_percent_candidates.csv``
    * ``top_low_drawdown_candidates.csv``
    * ``top_walk_forward_candidates.csv``

Depends on pandas/numpy only. No ML, no UI, and it never modifies the v1.2
outputs or any existing application behaviour.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

# Repo root: run_robustness_diagnostics.py -> strategy_lab -> app -> backend -> ROOT
REPO_ROOT = Path(__file__).resolve().parents[3]
INPUT_DIR = REPO_ROOT / "MetaTrader_Data" / "reports" / "risk_backtests_v1_2"
OUTPUT_DIR = REPO_ROOT / "MetaTrader_Data" / "reports" / "risk_backtests_v1_3"

# XAUUSD contract size used by the v1.2 runner (1.0 lot controls 100 oz). The
# stored ``notional`` column already equals ``entry_price * CONTRACT_SIZE * lots``;
# we recompute ``entry_notional`` from this constant so the formula stays explicit
# and the diagnostics do not silently depend on a derived column.
CONTRACT_SIZE = 100.0

# v1.2 summary fields requirement 1 guarantees are present. Verified, not edited.
REQUIRED_SUMMARY_FIELDS: tuple[str, ...] = (
    "timeframe",
    "strategy_family",
    "strategy_label",
    "direction",
    "exit_mode",
    "sizing_mode",
    "leverage",
    "final_equity",
    "total_return_pct",
    "profit_factor",
    "max_drawdown_pct",
    "min_margin_level",
    "stop_out_count",
    "insufficient_margin_count",
)

# Identity + parameter columns that define a config *except* leverage. Two runs
# that share all of these differ only by broker leverage, so grouping on them is
# exactly the leverage-comparison grouping requested by the spec.
GROUP_KEYS: tuple[str, ...] = (
    "timeframe",
    "strategy_family",
    "strategy_label",
    "direction",
    "exit_mode",
    "stop_loss_atr",
    "take_profit_atr",
    "initial_stop_loss_atr",
    "trailing_stop_atr",
    "max_holding_bars",
    "sizing_mode",
    "fixed_lot",
    "risk_percent",
)

# v1.2 identity/param columns carried onto per-config diagnostic tables so the
# standalone CSVs are readable without re-joining the summary.
PARAM_COLUMNS: tuple[str, ...] = ("config_id", *GROUP_KEYS, "leverage")

# Fixed walk-forward splits (inclusive year ranges) requested by the spec.
WALK_FORWARD_SPLITS: tuple[tuple[str, int, int], ...] = (
    ("2015-2018", 2015, 2018),
    ("2019-2021", 2019, 2021),
    ("2022-2024", 2022, 2024),
    ("2025-2026", 2025, 2026),
)

# A final-equity gap larger than this (account currency) is treated as a real
# PnL difference rather than floating-point dust. Identical trade sets produce
# bit-identical balances, so any non-trivial gap means leverage actually changed
# *which* trades were taken (margin gating / stop-out), never the lot size.
MATERIAL_EQUITY_DIFF = 0.01

# Only columns we actually need from the (large) trades.csv.
_TRADE_USECOLS = [
    "config_id",
    "exit_time",
    "net_pnl",
    "balance_after_trade",
    "equity_after_trade",
    "exit_reason",
    "lots",
    "entry_price",
    "required_margin",
]

_SKIPPED_USECOLS = ["config_id", "entry_time", "skipped_reason"]


# ---------------------------------------------------------------------------
# Inputs (requirement 1: verify the v1.2 outputs)
# ---------------------------------------------------------------------------

def verify_v1_2_summary(input_dir: Path) -> pd.DataFrame:
    """Load ``summary.csv`` and assert every required v1.2 field is present."""
    path = input_dir / "summary.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"v1.2 summary not found: {path}. Run run_risk_backtests.py first."
        )
    summary = pd.read_csv(path)
    missing = [c for c in REQUIRED_SUMMARY_FIELDS if c not in summary.columns]
    if missing:
        raise ValueError(f"summary.csv is missing required v1.2 fields: {missing}")
    print(
        f"Verified v1.2 summary: {path}\n"
        f"  {len(summary)} configs, all {len(REQUIRED_SUMMARY_FIELDS)} required "
        f"fields present."
    )
    return summary


def load_trades(input_dir: Path) -> pd.DataFrame:
    """Load the executed-trade rows, parsing only the columns we need."""
    path = input_dir / "trades.csv"
    if not path.exists():
        raise FileNotFoundError(f"v1.2 trades not found: {path}.")
    trades = pd.read_csv(path, usecols=_TRADE_USECOLS)
    # Times were written as naive UTC wall time; parse back to datetime for years.
    trades["exit_time"] = pd.to_datetime(trades["exit_time"])
    return trades


def load_skipped(input_dir: Path) -> pd.DataFrame:
    """Load skipped entries (used for per-year insufficient_margin counts)."""
    path = input_dir / "skipped_trades.csv"
    if not path.exists():
        return pd.DataFrame(columns=_SKIPPED_USECOLS)
    skipped = pd.read_csv(path, usecols=_SKIPPED_USECOLS)
    return skipped


# ---------------------------------------------------------------------------
# Requirement 2: effective leverage (actual exposure vs. equity)
# ---------------------------------------------------------------------------

def _safe_ratio(numerator: pd.Series, denominator: pd.Series) -> np.ndarray:
    """Element-wise ``numerator / denominator``; NaN where denominator <= 0.

    Equity can in principle be wiped out by a stop-out, so a non-positive
    denominator would make the leverage ratio meaningless rather than huge.
    """
    num = numerator.to_numpy(dtype=float)
    den = denominator.to_numpy(dtype=float)
    out = np.full(len(den), np.nan)
    ok = den > 0
    out[ok] = num[ok] / den[ok]
    return out


def add_effective_leverage(trades: pd.DataFrame) -> pd.DataFrame:
    """Attach per-trade exposure columns.

    ``equity_after_trade`` equals the post-trade balance (one position at a
    time), so the pre-entry equity is simply ``balance_after_trade - net_pnl``.
    """
    entry_notional = trades["entry_price"] * CONTRACT_SIZE * trades["lots"]
    equity_before = trades["balance_after_trade"] - trades["net_pnl"]
    equity_after = trades["equity_after_trade"]

    trades = trades.copy()
    trades["entry_notional"] = entry_notional
    trades["equity_before_entry"] = equity_before
    trades["effective_leverage_at_entry"] = _safe_ratio(entry_notional, equity_before)
    trades["effective_leverage_at_exit"] = _safe_ratio(entry_notional, equity_after)
    return trades


def effective_leverage_summary(trades: pd.DataFrame) -> pd.DataFrame:
    """Per-config exposure aggregates.

    The headline ``*_effective_leverage`` figures use the *at-entry* ratio --
    exposure measured against the equity you held the position with, which is
    the conventional read of "how leveraged was this strategy".
    """
    grouped = trades.groupby("config_id", sort=False)
    out = grouped.agg(
        average_effective_leverage=("effective_leverage_at_entry", "mean"),
        max_effective_leverage=("effective_leverage_at_entry", "max"),
        median_effective_leverage=("effective_leverage_at_entry", "median"),
        average_required_margin=("required_margin", "mean"),
        max_required_margin=("required_margin", "max"),
    ).reset_index()
    return out


# ---------------------------------------------------------------------------
# Requirement 3: leverage comparison diagnostics
# ---------------------------------------------------------------------------

def leverage_comparison(summary: pd.DataFrame) -> pd.DataFrame:
    """Compare leverage variants within each otherwise-identical config group.

    ``dropna=False`` is essential: most parameter columns are NaN for the exit /
    sizing modes that do not use them (e.g. ``risk_percent`` is NaN for fixed-lot
    runs), and pandas would otherwise silently drop those groups entirely.
    """
    grouped = summary.groupby(list(GROUP_KEYS), dropna=False, sort=False)
    out = grouped.agg(
        min_leverage=("leverage", "min"),
        max_leverage=("leverage", "max"),
        number_of_leverage_variants=("leverage", "nunique"),
        final_equity_min=("final_equity", "min"),
        final_equity_max=("final_equity", "max"),
        max_drawdown_pct_min=("max_drawdown_pct", "min"),
        max_drawdown_pct_max=("max_drawdown_pct", "max"),
        min_margin_level_min=("min_margin_level", "min"),
        insufficient_margin_count_max=("insufficient_margin_count", "max"),
        stop_out_count_max=("stop_out_count", "max"),
    ).reset_index()

    out["final_equity_diff"] = out["final_equity_max"] - out["final_equity_min"]
    # True only when the gap is materially non-zero (real, not rounding noise).
    out["pnl_changed_by_leverage"] = out["final_equity_diff"] > MATERIAL_EQUITY_DIFF

    ordered = [
        *GROUP_KEYS,
        "min_leverage",
        "max_leverage",
        "number_of_leverage_variants",
        "final_equity_min",
        "final_equity_max",
        "final_equity_diff",
        "max_drawdown_pct_min",
        "max_drawdown_pct_max",
        "min_margin_level_min",
        "insufficient_margin_count_max",
        "stop_out_count_max",
        "pnl_changed_by_leverage",
    ]
    return out[ordered]


# ---------------------------------------------------------------------------
# Requirements 4 & 5: yearly + walk-forward robustness
# ---------------------------------------------------------------------------

def _period_breakdown(trades: pd.DataFrame, period_col: str) -> pd.DataFrame:
    """Per ``(config_id, period_col)`` equity / return / drawdown / PnL metrics.

    ``trades`` must be pre-sorted by ``(config_id, exit_time)`` with a clean
    RangeIndex. Realised PnL is attributed by ``exit_time`` (the bar where it is
    booked). Each period's drawdown resets its peak to the period's *opening*
    equity, so it measures risk taken *within* that period rather than dragging
    in earlier highs.
    """
    keys = ["config_id", period_col]
    grouped = trades.groupby(keys, dropna=False, sort=False)

    balance = trades["balance_after_trade"]
    net_pnl = trades["net_pnl"]
    # Opening equity of each period == balance before that period's first trade.
    first_balance = grouped["balance_after_trade"].transform("first")
    first_pnl = grouped["net_pnl"].transform("first")
    start_equity = first_balance - first_pnl

    # Running peak within the period, floored by the period's opening equity.
    running_peak = np.maximum(grouped["balance_after_trade"].cummax(), start_equity)
    dd_pct = np.where(
        running_peak > 0, (running_peak - balance) / running_peak * 100.0, 0.0
    )

    work = trades.assign(
        _start_equity=start_equity,
        _dd_pct=dd_pct,
        _gross_profit=net_pnl.clip(lower=0.0),
        _gross_loss=(-net_pnl).clip(lower=0.0),
        _stop_out=(trades["exit_reason"] == "stop_out").astype(int),
    )
    agg = work.groupby(keys, dropna=False, sort=False).agg(
        start_equity=("_start_equity", "first"),
        end_equity=("balance_after_trade", "last"),
        max_drawdown_pct=("_dd_pct", "max"),
        trades_count=("net_pnl", "size"),
        net_profit=("net_pnl", "sum"),
        gross_profit=("_gross_profit", "sum"),
        gross_loss=("_gross_loss", "sum"),
        stop_out_count=("_stop_out", "sum"),
    ).reset_index()

    agg["return_pct"] = np.where(
        agg["start_equity"] > 0,
        (agg["end_equity"] - agg["start_equity"]) / agg["start_equity"] * 100.0,
        np.nan,
    )
    # Profit factor only when feasible (needs at least one losing trade).
    agg["profit_factor"] = np.where(
        agg["gross_loss"] > 0, agg["gross_profit"] / agg["gross_loss"], np.nan
    )
    return agg.drop(columns=["gross_profit", "gross_loss"])


def _insufficient_by_year(skipped: pd.DataFrame) -> pd.DataFrame:
    """Count insufficient-margin skips per ``(config_id, year)`` by entry time."""
    cols = ["config_id", "year", "insufficient_margin_count"]
    if skipped.empty:
        return pd.DataFrame(columns=cols)
    insuff = skipped[skipped["skipped_reason"] == "insufficient_margin"].copy()
    if insuff.empty:
        return pd.DataFrame(columns=cols)
    insuff["year"] = pd.to_datetime(insuff["entry_time"]).dt.year
    return (
        insuff.groupby(["config_id", "year"], sort=False)
        .size()
        .reset_index(name="insufficient_margin_count")
    )


def yearly_summary(trades: pd.DataFrame, skipped: pd.DataFrame) -> pd.DataFrame:
    """Per ``(config_id, year)`` robustness breakdown.

    Years are taken from each trade's ``exit_time``. ``insufficient_margin_count``
    is merged in from skipped entries; years that *only* had skips (no executed
    trades) do not appear here -- those configs are filtered out of the rankings
    anyway, so the simpler trade-driven index is sufficient.
    """
    yearly = trades.assign(year=trades["exit_time"].dt.year)
    breakdown = _period_breakdown(yearly, "year").rename(
        columns={
            "return_pct": "yearly_return_pct",
            "max_drawdown_pct": "yearly_max_drawdown_pct",
        }
    )
    breakdown = breakdown.merge(
        _insufficient_by_year(skipped), on=["config_id", "year"], how="left"
    )
    breakdown["insufficient_margin_count"] = (
        breakdown["insufficient_margin_count"].fillna(0).astype(int)
    )
    cols = [
        "config_id",
        "year",
        "start_equity",
        "end_equity",
        "yearly_return_pct",
        "yearly_max_drawdown_pct",
        "trades_count",
        "net_profit",
        "profit_factor",
        "stop_out_count",
        "insufficient_margin_count",
    ]
    return breakdown[cols]


def _assign_split(years: pd.Series) -> pd.Series:
    """Map each year to its walk-forward split label (NA outside the splits)."""
    split = pd.Series(pd.NA, index=years.index, dtype="object")
    for label, low, high in WALK_FORWARD_SPLITS:
        split = split.mask((years >= low) & (years <= high), label)
    return split


def walk_forward_summary(trades: pd.DataFrame) -> pd.DataFrame:
    """Per ``(config_id, split)`` robustness breakdown over fixed date splits."""
    split = _assign_split(trades["exit_time"].dt.year)
    in_split = trades.assign(split=split)
    in_split = in_split[in_split["split"].notna()].reset_index(drop=True)

    breakdown = _period_breakdown(in_split, "split").rename(
        columns={
            "return_pct": "period_return_pct",
            "max_drawdown_pct": "period_max_drawdown_pct",
        }
    )
    cols = [
        "config_id",
        "split",
        "start_equity",
        "end_equity",
        "period_return_pct",
        "period_max_drawdown_pct",
        "trades_count",
        "net_profit",
        "profit_factor",
    ]
    return breakdown[cols]


# ---------------------------------------------------------------------------
# Requirement 7: return-concentration diagnostics
# ---------------------------------------------------------------------------

def concentration_summary(yearly: pd.DataFrame, summary: pd.DataFrame) -> pd.DataFrame:
    """Per-config concentration metrics: did one exceptional year carry it?

    ``return_concentration_ratio = best_year_profit / total_net_profit``. The
    ratio is only meaningful when the strategy is net profitable, so it is NaN
    when total net profit is non-positive (a single huge year against losses
    elsewhere would otherwise produce a misleading value).
    """
    grouped = yearly.groupby("config_id", sort=False)
    out = grouped.agg(
        profitable_years=("net_profit", lambda s: int((s > 0).sum())),
        losing_years=("net_profit", lambda s: int((s < 0).sum())),
        best_year_return_pct=("yearly_return_pct", "max"),
        worst_year_return_pct=("yearly_return_pct", "min"),
        best_year_profit=("net_profit", "max"),
    ).reset_index()

    totals = summary[["config_id", "net_profit"]].rename(
        columns={"net_profit": "total_net_profit"}
    )
    out = out.merge(totals, on="config_id", how="left")
    out["return_concentration_ratio"] = np.where(
        out["total_net_profit"] > 0,
        out["best_year_profit"] / out["total_net_profit"],
        np.nan,
    )
    return out


# ---------------------------------------------------------------------------
# Requirement 6: candidate rankings
# ---------------------------------------------------------------------------

def enrich_summary(
    summary: pd.DataFrame,
    eff_lev: pd.DataFrame,
    concentration: pd.DataFrame,
) -> pd.DataFrame:
    """Attach effective-leverage and concentration context to each summary row."""
    eff_cols = [
        "config_id",
        "average_effective_leverage",
        "max_effective_leverage",
        "median_effective_leverage",
        "average_required_margin",
        "max_required_margin",
    ]
    conc_cols = [
        "config_id",
        "profitable_years",
        "losing_years",
        "best_year_return_pct",
        "worst_year_return_pct",
        "return_concentration_ratio",
    ]
    out = summary.merge(eff_lev[eff_cols], on="config_id", how="left")
    out = out.merge(concentration[conc_cols], on="config_id", how="left")
    return out


def top_fixed_lot(enriched: pd.DataFrame) -> pd.DataFrame:
    """Robust fixed-lot candidates (req. 6 filters), best risk score first."""
    mask = (
        (enriched["sizing_mode"] == "fixed_lot")
        & (enriched["total_trades"] >= 50)
        & (enriched["stop_out_count"] == 0)
        & (enriched["insufficient_margin_count"] == 0)
        & (enriched["max_drawdown_pct"] <= 35)
    )
    return (
        enriched[mask]
        .sort_values("risk_adjusted_score", ascending=False)
        .reset_index(drop=True)
    )


def top_risk_percent(enriched: pd.DataFrame) -> pd.DataFrame:
    """Robust risk-percent candidates (req. 6 filters), best risk score first."""
    mask = (
        (enriched["sizing_mode"] == "risk_percent")
        & (enriched["total_trades"] >= 50)
        & (enriched["stop_out_count"] == 0)
        & (enriched["insufficient_margin_count"] == 0)
        & (enriched["max_drawdown_pct"] <= 35)
    )
    return (
        enriched[mask]
        .sort_values("risk_adjusted_score", ascending=False)
        .reset_index(drop=True)
    )


def top_low_drawdown(enriched: pd.DataFrame) -> pd.DataFrame:
    """Low-drawdown candidates: shallow DD, decent PF, enough trades, no stop-out."""
    mask = (
        (enriched["max_drawdown_pct"] <= 15)
        & (enriched["profit_factor"] >= 1.3)
        & (enriched["total_trades"] >= 50)
        & (enriched["stop_out_count"] == 0)
    )
    return (
        enriched[mask]
        .sort_values(
            ["max_drawdown_pct", "risk_adjusted_score"], ascending=[True, False]
        )
        .reset_index(drop=True)
    )


def top_walk_forward(
    walk_forward: pd.DataFrame, enriched: pd.DataFrame
) -> pd.DataFrame:
    """Candidates that hold up across the walk-forward splits.

    A "profitable period" is one with positive net profit. Requires profit in at
    least 3 of the 4 splits, no split drawdown above 35%, >= 50 trades and zero
    stop-outs overall.
    """
    agg = (
        walk_forward.groupby("config_id", sort=False)
        .agg(
            walk_forward_periods=("split", "nunique"),
            profitable_periods=("net_profit", lambda s: int((s > 0).sum())),
            worst_period_drawdown_pct=("period_max_drawdown_pct", "max"),
            walk_forward_min_return_pct=("period_return_pct", "min"),
        )
        .reset_index()
    )
    merged = enriched.merge(agg, on="config_id", how="inner")
    mask = (
        (merged["profitable_periods"] >= 3)
        & (merged["worst_period_drawdown_pct"] <= 35)
        & (merged["total_trades"] >= 50)
        & (merged["stop_out_count"] == 0)
    )
    return (
        merged[mask]
        .sort_values(
            ["profitable_periods", "risk_adjusted_score"], ascending=[False, False]
        )
        .reset_index(drop=True)
    )


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def with_params(metrics: pd.DataFrame, summary: pd.DataFrame) -> pd.DataFrame:
    """Prepend the v1.2 identity/param columns to a per-config metrics frame."""
    params = summary[list(PARAM_COLUMNS)]
    return params.merge(metrics, on="config_id", how="inner")


# ---------------------------------------------------------------------------
# Requirement 8: console report
# ---------------------------------------------------------------------------

_CANDIDATE_COLUMNS = [
    "config_id",
    "total_trades",
    "profit_factor",
    "total_return_pct",
    "max_drawdown_pct",
    "final_equity",
    "average_effective_leverage",
    "max_effective_leverage",
    "return_concentration_ratio",
    "risk_adjusted_score",
]
_WALK_FORWARD_EXTRA = ["profitable_periods", "worst_period_drawdown_pct"]


def _print_candidates(
    df: pd.DataFrame, title: str, *, n: int = 10, extra: tuple[str, ...] = ()
) -> None:
    print(f"\n=== {title} (top {n}) ===")
    if df is None or df.empty:
        print("(none passed the filters)")
        return
    cols = [c for c in (*_CANDIDATE_COLUMNS, *extra) if c in df.columns]
    with pd.option_context("display.max_columns", None, "display.width", 240):
        print(df.head(n)[cols].to_string(index=False))


def print_report(
    output_dir: Path,
    fixed_lot: pd.DataFrame,
    risk_percent: pd.DataFrame,
    low_drawdown: pd.DataFrame,
    walk_forward_cands: pd.DataFrame,
    lev_cmp: pd.DataFrame,
) -> None:
    """Print the end-of-run console summary requested by the spec."""
    print(f"\nOutput folder: {output_dir}")

    _print_candidates(fixed_lot, "Top fixed-lot candidates")
    _print_candidates(risk_percent, "Top risk-percent candidates")
    _print_candidates(low_drawdown, "Top low-drawdown candidates")
    _print_candidates(
        walk_forward_cands,
        "Top walk-forward candidates",
        extra=tuple(_WALK_FORWARD_EXTRA),
    )

    n_groups = len(lev_cmp)
    n_multi = int((lev_cmp["number_of_leverage_variants"] > 1).sum())
    n_changed = int(lev_cmp["pnl_changed_by_leverage"].sum())
    print("\n=== Leverage comparison summary ===")
    print(f"Config groups (all params except leverage): {n_groups}")
    print(f"Groups holding >1 leverage variant:         {n_multi}")
    print(f"Groups where PnL changed by leverage:       {n_changed}")
    print(
        "(Leverage alone never changes PnL at a fixed lot size; a non-zero count\n"
        " reflects margin gating / stop-out altering which trades actually ran.)"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Strategy Lab v1.3 robustness & leverage diagnostics"
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=INPUT_DIR,
        help="Directory holding the v1.2 risk-backtest CSVs (read-only).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=OUTPUT_DIR,
        help="Directory to write the v1.3 diagnostic CSVs into.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    in_dir = args.input_dir
    out_dir = args.output_dir

    # 1. Verify the v1.2 inputs, then load them.
    summary = verify_v1_2_summary(in_dir)
    print("Loading trades.csv (the large v1.2 output) ...")
    trades = add_effective_leverage(load_trades(in_dir))
    # Period analytics need trades ordered within each config; sort once.
    trades = trades.sort_values(["config_id", "exit_time"]).reset_index(drop=True)
    skipped = load_skipped(in_dir)
    print(
        f"  {len(trades)} trades across {trades['config_id'].nunique()} configs; "
        f"{len(skipped)} skipped entries."
    )

    # 2-7. Diagnostics.
    eff_lev = effective_leverage_summary(trades)
    lev_cmp = leverage_comparison(summary)
    yearly = yearly_summary(trades, skipped)
    walk_forward = walk_forward_summary(trades)
    concentration = concentration_summary(yearly, summary)

    enriched = enrich_summary(summary, eff_lev, concentration)
    fixed_lot = top_fixed_lot(enriched)
    risk_percent = top_risk_percent(enriched)
    low_drawdown = top_low_drawdown(enriched)
    walk_forward_cands = top_walk_forward(walk_forward, enriched)

    # 8. Write outputs.
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[tuple[str, pd.DataFrame]] = [
        ("effective_leverage_summary.csv", with_params(eff_lev, summary)),
        ("leverage_comparison.csv", lev_cmp),
        ("yearly_summary.csv", yearly),
        ("walk_forward_summary.csv", walk_forward),
        ("concentration_summary.csv", with_params(concentration, summary)),
        ("top_fixed_lot_candidates.csv", fixed_lot),
        ("top_risk_percent_candidates.csv", risk_percent),
        ("top_low_drawdown_candidates.csv", low_drawdown),
        ("top_walk_forward_candidates.csv", walk_forward_cands),
    ]
    print()
    for name, frame in outputs:
        path = out_dir / name
        frame.to_csv(path, index=False)
        print(f"Wrote {len(frame):>6} rows -> {path}")

    print_report(
        out_dir, fixed_lot, risk_percent, low_drawdown, walk_forward_cands, lev_cmp
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
