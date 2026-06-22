"""Focused account-based backtest runner (Strategy Lab v1.2).

Runs a *bounded, reproducible sample* of realistic leverage/margin backtests for
the strongest XAUUSD candidates and writes research CSVs + console leaderboards.

The full parameter space (timeframe x strategy x direction x exit x sizing x
leverage) is far larger than anyone wants to run end-to-end, so the runner draws
a deterministic random sample capped at ``--max-runs`` (default 3000). This is a
*research* tool only -- it never trades.

Run it directly::

    python backend/app/strategy_lab/run_risk_backtests.py

or as a module from the ``backend`` directory::

    python -m app.strategy_lab.run_risk_backtests

Outputs (under ``MetaTrader_Data/reports/risk_backtests_v1_2/``):
    * ``trades.csv``          - every executed trade across all sampled runs.
    * ``equity_curves.csv``   - per-bar equity curves for the top candidates.
    * ``summary.csv``         - one row of metrics per sampled run.
    * ``top_candidates.csv``  - filtered + ranked research candidates.
    * ``skipped_trades.csv``  - entries skipped (e.g. insufficient_margin).
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path
from typing import Callable, Iterable

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

# Repo root: run_risk_backtests.py -> strategy_lab -> app -> backend -> ROOT
REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "MetaTrader_Data" / "mt5_exports"
OUTPUT_DIR = REPO_ROOT / "MetaTrader_Data" / "reports" / "risk_backtests_v1_2"

# Source file per timeframe (read-only; never modified).
TIMEFRAME_FILES: dict[str, str] = {
    "H1": "XAUUSDrfd_H1.csv",
    "H4": "XAUUSDrfd_H4.csv",
}

# Deterministic sampling seed so a given --max-runs always picks the same runs.
SAMPLE_SEED = 42

# ATR period for stops / risk sizing (matches the v1 backtester default).
ATR_PERIOD = 14

# How many top candidates to materialise full equity curves for. A per-bar
# curve spans the whole dataset (~67k rows on H1 over 10y), so writing one for
# *every* run would be many gigabytes. Only the best candidates' curves are
# useful for research, so we cap here (each H1 curve is roughly 10-15 MB).
EQUITY_CURVE_LIMIT = 10

# Defaults shared by every run (the v1.2 account assumptions).
ACCOUNT_DEFAULTS = dict(
    contract_size=100.0,
    point_value=0.01,
    fixed_spread_points=30.0,
    commission_per_lot_round_turn=0.0,
    swap_long_per_lot_per_day=0.0,
    swap_short_per_lot_per_day=0.0,
)


# ---------------------------------------------------------------------------
# Strategy / parameter universe
# ---------------------------------------------------------------------------

# (family, label, strategy-params). ``label`` is the per-variant strategy name.
STRATEGIES: list[tuple[str, str, dict]] = [
    ("donchian", "donchian_55", {"lookback": 55}),
    ("donchian", "donchian_80", {"lookback": 80}),
    ("donchian", "donchian_100", {"lookback": 100}),
    ("ema", "ema_30_100", {"fast_period": 30, "slow_period": 100}),
    ("ema", "ema_50_100", {"fast_period": 50, "slow_period": 100}),
    ("ema", "ema_50_150", {"fast_period": 50, "slow_period": 150}),
    ("ema", "ema_50_200", {"fast_period": 50, "slow_period": 200}),
    ("supertrend", "supertrend_10_2.0", {"atr_period": 10, "multiplier": 2.0}),
    ("supertrend", "supertrend_10_2.5", {"atr_period": 10, "multiplier": 2.5}),
    ("supertrend", "supertrend_14_2.0", {"atr_period": 14, "multiplier": 2.0}),
]

_SIGNAL_FUNCS: dict[str, Callable[..., pd.DataFrame]] = {
    "donchian": strategies.donchian_breakout_strategy,
    "ema": strategies.ema_crossover_strategy,
    "supertrend": strategies.supertrend_strategy,
}

DIRECTIONS: tuple[str, ...] = ("long_only", "short_only", "both")
LEVERAGES: tuple[float, ...] = (1.0, 5.0, 10.0, 20.0, 50.0)


def build_sizing_grid() -> list[dict]:
    """Position-sizing variants: fixed lot and risk-percent."""
    grid: list[dict] = []
    for fixed_lot in (0.01, 0.05, 0.10):
        grid.append({"sizing_mode": "fixed_lot", "fixed_lot": fixed_lot})
    for risk_percent in (0.25, 0.5, 1.0):
        grid.append({"sizing_mode": "risk_percent", "risk_percent": risk_percent})
    return grid


def build_exit_grid(timeframe: str) -> list[dict]:
    """Exit-rule variants. Time-exit holding horizons depend on the timeframe."""
    exits: list[dict] = []

    # A. fixed ATR stop / target
    for sl in (2.0, 3.0, 4.0, 5.0, 7.0, 10.0):
        for tp in (5.0, 8.0, 12.0, 16.0, 20.0, 30.0):
            exits.append(
                {"exit_mode": "fixed_atr", "stop_loss_atr": sl, "take_profit_atr": tp}
            )

    # B. ATR trailing stop (optional take-profit; None disables it)
    for isl in (2.0, 3.0, 4.0, 5.0):
        for ts in (3.0, 4.0, 5.0, 7.0, 10.0):
            for tp in (None, 12.0, 20.0, 30.0):
                exits.append(
                    {
                        "exit_mode": "atr_trailing",
                        "initial_stop_loss_atr": isl,
                        "trailing_stop_atr": ts,
                        "take_profit_atr": tp,
                    }
                )

    # C. time / max-holding exit
    holds = (48, 120, 240, 480) if timeframe == "H1" else (24, 72, 120, 240)
    for sl in (3.0, 5.0, 7.0):
        for tp in (None, 12.0, 20.0, 30.0):
            for mh in holds:
                exits.append(
                    {
                        "exit_mode": "time_exit",
                        "stop_loss_atr": sl,
                        "take_profit_atr": tp,
                        "max_holding_bars": mh,
                    }
                )

    return exits


# ---------------------------------------------------------------------------
# Run specification
# ---------------------------------------------------------------------------

# One run is a lightweight tuple so the *full* space can be built and sampled
# cheaply before any (heavier) RiskConfig objects are materialised.
#   (timeframe, strategy_index, direction, exit_params, sizing_params, leverage)
RunTuple = tuple[str, int, str, dict, dict, float]


def build_full_space(
    timeframes: Iterable[str],
    strategy_indices: Iterable[int],
    directions: Iterable[str],
    leverages: Iterable[float],
) -> list[RunTuple]:
    """Enumerate every run combination for the (possibly filtered) axes."""
    sizing_grid = build_sizing_grid()
    runs: list[RunTuple] = []
    for tf in timeframes:
        exit_grid = build_exit_grid(tf)
        for s_idx in strategy_indices:
            for direction in directions:
                for exit_p in exit_grid:
                    for sizing_p in sizing_grid:
                        for lev in leverages:
                            runs.append((tf, s_idx, direction, exit_p, sizing_p, lev))
    return runs


def make_config(run: RunTuple, initial_equity: float) -> RiskConfig:
    """Build the :class:`RiskConfig` for one run tuple."""
    tf, _s_idx, direction, exit_p, sizing_p, lev = run
    kwargs = dict(
        ACCOUNT_DEFAULTS,
        initial_equity=initial_equity,
        leverage=lev,
        direction_mode=direction,
        atr_period=ATR_PERIOD,
    )
    kwargs.update(exit_p)
    kwargs.update(sizing_p)
    return RiskConfig(**kwargs)


def make_config_id(run: RunTuple) -> str:
    """Stable, human-readable identifier for one run."""
    tf, s_idx, direction, exit_p, sizing_p, lev = run
    label = STRATEGIES[s_idx][1]

    mode = exit_p["exit_mode"]
    if mode == "fixed_atr":
        exit_str = f"fixed_sl{exit_p['stop_loss_atr']:g}_tp{exit_p['take_profit_atr']:g}"
    elif mode == "atr_trailing":
        tp = exit_p["take_profit_atr"]
        exit_str = (
            f"trail_isl{exit_p['initial_stop_loss_atr']:g}"
            f"_ts{exit_p['trailing_stop_atr']:g}_tp{('none' if tp is None else f'{tp:g}')}"
        )
    else:  # time_exit
        tp = exit_p["take_profit_atr"]
        exit_str = (
            f"time_sl{exit_p['stop_loss_atr']:g}"
            f"_tp{('none' if tp is None else f'{tp:g}')}_mh{exit_p['max_holding_bars']}"
        )

    if sizing_p["sizing_mode"] == "fixed_lot":
        sizing_str = f"lot{sizing_p['fixed_lot']:g}"
    else:
        sizing_str = f"risk{sizing_p['risk_percent']:g}"

    return f"{tf}|{label}|{direction}|{exit_str}|{sizing_str}|lev{lev:g}"


def summary_param_columns(run: RunTuple) -> dict:
    """Flat parameter columns for the summary row (easy filtering in a CSV)."""
    tf, s_idx, direction, exit_p, sizing_p, lev = run
    family, label, _ = STRATEGIES[s_idx]
    return {
        "timeframe": tf,
        "strategy_family": family,
        "strategy_label": label,
        "direction": direction,
        "exit_mode": exit_p["exit_mode"],
        "stop_loss_atr": exit_p.get("stop_loss_atr"),
        "take_profit_atr": exit_p.get("take_profit_atr"),
        "initial_stop_loss_atr": exit_p.get("initial_stop_loss_atr"),
        "trailing_stop_atr": exit_p.get("trailing_stop_atr"),
        "max_holding_bars": exit_p.get("max_holding_bars"),
        "sizing_mode": sizing_p["sizing_mode"],
        "fixed_lot": sizing_p.get("fixed_lot"),
        "risk_percent": sizing_p.get("risk_percent"),
        "leverage": lev,
    }


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------

def precompute_signals(
    timeframes: Iterable[str],
    strategy_indices: Iterable[int],
) -> tuple[dict, dict, dict, str | None]:
    """Load data and compute signals + ATR once per (timeframe, strategy).

    Returns ``(df_cache, atr_cache, signal_cache, symbol)`` where ``signal_cache``
    is keyed by ``(timeframe, strategy_label)``.
    """
    df_cache: dict[str, pd.DataFrame] = {}
    atr_cache: dict[str, "pd.Series"] = {}
    signal_cache: dict[tuple[str, str], pd.DataFrame] = {}
    symbol: str | None = None

    for tf in timeframes:
        path = DATA_DIR / TIMEFRAME_FILES[tf]
        print(f"Loading {path} ...")
        df = data_loader.load_mt5_csv(path)
        print(
            f"  {len(df)} bars "
            f"({df['datetime'].iloc[0]} -> {df['datetime'].iloc[-1]})"
        )
        df_cache[tf] = df
        atr_cache[tf] = indicators.atr(df, ATR_PERIOD).to_numpy(dtype=float)
        if symbol is None and len(df):
            symbol = df["symbol"].iloc[0]

        for s_idx in strategy_indices:
            family, label, sparams = STRATEGIES[s_idx]
            signal_cache[(tf, label)] = _SIGNAL_FUNCS[family](df, **sparams)

    return df_cache, atr_cache, signal_cache, symbol


def run_sweep(
    sampled: list[RunTuple],
    *,
    df_cache: dict,
    atr_cache: dict,
    signal_cache: dict,
    symbol: str | None,
    initial_equity: float,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    """Execute every sampled run; return (trades, skipped, summary, run_by_id)."""
    all_trades: list[pd.DataFrame] = []
    all_skipped: list[pd.DataFrame] = []
    summary_rows: list[dict] = []
    run_by_id: dict[str, RunTuple] = {}

    total = len(sampled)
    for n, run in enumerate(sampled, start=1):
        tf, s_idx, direction, _exit_p, _sizing_p, _lev = run
        label = STRATEGIES[s_idx][1]
        config = make_config(run, initial_equity)
        config_id = make_config_id(run)
        run_by_id[config_id] = run

        trades, equity_curve, skipped = risk_backtester.run_risk_backtest(
            df_cache[tf],
            signal_cache[(tf, label)],
            config,
            strategy_name=label,
            symbol=symbol,
            timeframe=tf,
            atr_values=atr_cache[tf],
        )

        run_metrics = metrics.compute_risk_metrics(
            trades, equity_curve, skipped, initial_equity=initial_equity
        )

        if len(trades):
            trades = trades.copy()
            trades.insert(0, "config_id", config_id)
            all_trades.append(trades)
        if len(skipped):
            skipped = skipped.copy()
            skipped.insert(0, "config_id", config_id)
            all_skipped.append(skipped)

        summary_rows.append(
            {"config_id": config_id, **summary_param_columns(run), **run_metrics}
        )

        if n % 50 == 0:
            print(f"  ... {n}/{total} runs")

    trades_df = (
        pd.concat(all_trades, ignore_index=True)
        if all_trades
        else pd.DataFrame(columns=["config_id", *risk_backtester.RISK_TRADE_COLUMNS])
    )
    skipped_df = (
        pd.concat(all_skipped, ignore_index=True)
        if all_skipped
        else pd.DataFrame(columns=["config_id", *risk_backtester.SKIPPED_COLUMNS])
    )
    summary_df = pd.DataFrame(summary_rows)
    return trades_df, skipped_df, summary_df, run_by_id


# ---------------------------------------------------------------------------
# Ranking / candidates
# ---------------------------------------------------------------------------

def add_risk_adjusted_score(summary: pd.DataFrame) -> pd.DataFrame:
    """Attach the research ranking score (defined in the v1.2 spec).

    ``profit_factor`` can be +inf (no losing trades); it is clipped to a finite
    value purely so the score sorts sensibly. The score is for *ranking research
    candidates only* -- it is not a performance guarantee.
    """
    if summary.empty:
        summary["risk_adjusted_score"] = []
        return summary

    pf = summary["profit_factor"].clip(upper=100.0).fillna(0.0)
    trades_capped = summary["total_trades"].clip(upper=500)
    summary = summary.copy()
    summary["risk_adjusted_score"] = (
        summary["total_return_pct"]
        + pf * 20.0
        - summary["max_drawdown_pct"] * 1.5
        - summary["stop_out_count"] * 100.0
        + trades_capped / 20.0
    )
    return summary


def select_candidates(summary: pd.DataFrame, initial_equity: float) -> pd.DataFrame:
    """Filter to viable research candidates and rank by risk_adjusted_score."""
    if summary.empty:
        return summary
    mask = (
        (summary["total_trades"] >= 50)
        & (summary["final_equity"] > initial_equity)
        & (summary["profit_factor"] >= 1.05)
        & (summary["max_drawdown_pct"] <= 50.0)
        & (summary["stop_out_count"] == 0)
        & (summary["min_margin_level"] >= 1.0)
    )
    return (
        summary[mask]
        .sort_values("risk_adjusted_score", ascending=False)
        .reset_index(drop=True)
    )


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

_LEADERBOARD_COLUMNS = [
    "config_id",
    "total_trades",
    "win_rate",
    "profit_factor",
    "total_return_pct",
    "max_drawdown_pct",
    "final_equity",
    "min_margin_level",
    "stop_out_count",
    "risk_adjusted_score",
]


def _print_table(df: pd.DataFrame, title: str, n: int = 20) -> None:
    print(f"\n=== {title} ===")
    if df is None or df.empty:
        print("(none)")
        return
    cols = [c for c in _LEADERBOARD_COLUMNS if c in df.columns]
    with pd.option_context("display.max_columns", None, "display.width", 240):
        print(df.head(n)[cols].to_string(index=False))


def print_report(
    summary: pd.DataFrame,
    candidates: pd.DataFrame,
    output_dir: Path,
    total_runs: int,
) -> None:
    """Print the end-of-run console summary requested by the v1.2 spec."""
    print(f"\nTotal runs executed: {total_runs}")
    print(f"Output folder: {output_dir}")

    _print_table(candidates, "Top 20 candidates by risk_adjusted_score")

    if not summary.empty:
        by_equity = summary.sort_values("final_equity", ascending=False)
        _print_table(by_equity, "Top 20 by final_equity")

        profitable = summary[summary["final_equity"] > summary["initial_equity"]]
        by_dd = profitable.sort_values("max_drawdown_pct", ascending=True)
        _print_table(by_dd, "Top 20 lowest max_drawdown_pct (profitable runs)")

    if not candidates.empty:
        longs = candidates[candidates["direction"] == "long_only"]
        shorts = candidates[candidates["direction"] == "short_only"]
        _print_table(longs, "Best long-only candidates", n=10)
        _print_table(shorts, "Best short-only candidates", n=10)
    else:
        print("\n(no candidates passed the viability filters)")


# ---------------------------------------------------------------------------
# Equity curves for the best candidates
# ---------------------------------------------------------------------------

def build_candidate_equity_curves(
    candidates: pd.DataFrame,
    summary: pd.DataFrame,
    run_by_id: dict,
    *,
    df_cache: dict,
    atr_cache: dict,
    signal_cache: dict,
    symbol: str | None,
    initial_equity: float,
) -> pd.DataFrame:
    """Re-run the top candidates (cheap) to materialise their equity curves.

    Falls back to the best runs by ``final_equity`` when no candidate passed the
    viability filters, so the output file is still useful.
    """
    if not candidates.empty:
        chosen_ids = candidates["config_id"].head(EQUITY_CURVE_LIMIT).tolist()
    elif not summary.empty:
        chosen_ids = (
            summary.sort_values("final_equity", ascending=False)["config_id"]
            .head(5)
            .tolist()
        )
    else:
        chosen_ids = []

    frames: list[pd.DataFrame] = []
    for config_id in chosen_ids:
        run = run_by_id[config_id]
        tf, s_idx, *_ = run
        label = STRATEGIES[s_idx][1]
        config = make_config(run, initial_equity)
        _trades, equity_curve, _skipped = risk_backtester.run_risk_backtest(
            df_cache[tf],
            signal_cache[(tf, label)],
            config,
            strategy_name=label,
            symbol=symbol,
            timeframe=tf,
            atr_values=atr_cache[tf],
        )
        equity_curve = equity_curve.copy()
        equity_curve.insert(0, "config_id", config_id)
        frames.append(equity_curve)

    if frames:
        return pd.concat(frames, ignore_index=True)
    return pd.DataFrame(columns=["config_id", *risk_backtester.EQUITY_CURVE_COLUMNS])


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Strategy Lab v1.2 risk backtest runner")
    parser.add_argument("--max-runs", type=int, default=3000)
    parser.add_argument("--timeframe", choices=["H1", "H4"], default=None)
    parser.add_argument(
        "--strategy", choices=["donchian", "ema", "supertrend"], default=None
    )
    parser.add_argument(
        "--direction",
        choices=["long_only", "short_only", "both"],
        default=None,
    )
    parser.add_argument("--initial-equity", type=float, default=10000.0)
    parser.add_argument(
        "--leverage",
        type=float,
        default=None,
        help="Restrict the leverage axis to a single value.",
    )
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    timeframes = [args.timeframe] if args.timeframe else list(TIMEFRAME_FILES.keys())
    strategy_indices = [
        i
        for i, (family, _, _) in enumerate(STRATEGIES)
        if args.strategy is None or family == args.strategy
    ]
    directions = [args.direction] if args.direction else list(DIRECTIONS)
    leverages = [args.leverage] if args.leverage else list(LEVERAGES)

    full_space = build_full_space(timeframes, strategy_indices, directions, leverages)
    print(f"Full parameter space: {len(full_space)} combinations")

    # Deterministic sample so a given --max-runs always selects the same runs.
    if len(full_space) > args.max_runs:
        sampled = random.Random(SAMPLE_SEED).sample(full_space, args.max_runs)
        print(f"Sampling {args.max_runs} runs (seed={SAMPLE_SEED})")
    else:
        sampled = full_space
        print(f"Running all {len(sampled)} combinations (<= max_runs)")

    df_cache, atr_cache, signal_cache, symbol = precompute_signals(
        timeframes, strategy_indices
    )

    print(f"Executing {len(sampled)} backtests ...")
    trades_df, skipped_df, summary_df, run_by_id = run_sweep(
        sampled,
        df_cache=df_cache,
        atr_cache=atr_cache,
        signal_cache=signal_cache,
        symbol=symbol,
        initial_equity=args.initial_equity,
    )

    summary_df = add_risk_adjusted_score(summary_df)
    candidates_df = select_candidates(summary_df, args.initial_equity)

    equity_curves_df = build_candidate_equity_curves(
        candidates_df,
        summary_df,
        run_by_id,
        df_cache=df_cache,
        atr_cache=atr_cache,
        signal_cache=signal_cache,
        symbol=symbol,
        initial_equity=args.initial_equity,
    )

    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    trades_df.to_csv(out / "trades.csv", index=False)
    equity_curves_df.to_csv(out / "equity_curves.csv", index=False)
    summary_df.to_csv(out / "summary.csv", index=False)
    candidates_df.to_csv(out / "top_candidates.csv", index=False)
    skipped_df.to_csv(out / "skipped_trades.csv", index=False)

    print(f"\nWrote {len(trades_df)} trades            -> {out / 'trades.csv'}")
    print(f"Wrote {len(equity_curves_df)} equity-curve rows -> {out / 'equity_curves.csv'}")
    print(f"Wrote {len(summary_df)} summary rows       -> {out / 'summary.csv'}")
    print(f"Wrote {len(candidates_df)} candidates         -> {out / 'top_candidates.csv'}")
    print(f"Wrote {len(skipped_df)} skipped entries     -> {out / 'skipped_trades.csv'}")

    print_report(summary_df, candidates_df, out, total_runs=len(sampled))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
