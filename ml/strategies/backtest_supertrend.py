"""
Offline backtest + grid search for the SuperTrend rule-based strategy.

This script reads candle CSVs produced by ``download_bcs_goods_history.py``
and evaluates SuperTrend signal variants. Nothing here trains a model.

Strategy variants supported:

* ``long_only``           — enter long on bullish flip, exit on bearish flip / TP / SL / horizon
* ``short_only``          — enter short on bearish flip, exit on bullish flip / TP / SL / horizon
* ``long_short_reversal`` — flip position on every direction change (no TP/SL by default)
* ``filtered_*``          — same as the above with a minimum distance / volatility filter

Backtest assumptions
--------------------
* Entry at the signal candle's close (no look-ahead beyond OHLC close)
* commission_bps + slippage_bps applied per leg (roundtrip = 2x)
* If TP and SL both fall inside the same candle, SL is assumed to hit first
* One position at a time, no leverage, no pyramiding

Reports
-------
Writes JSON + CSV reports to ml/reports/strategies/, including:

* ``supertrend_<TICKER>_summary.json``       overview + selected/recommended candidate
* ``supertrend_<TICKER>_grid.csv``           full grid (train metrics + test metrics)
* ``supertrend_<TICKER>_best.json``          best train setup + its OOS test metrics
* ``supertrend_<TICKER>_walk_forward.json``  walk-forward fold metrics
"""
from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

# Allow running as a script from the repo root.
_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ml.strategies.supertrend import add_supertrend_signals, sanity_check  # noqa: E402


RAW_DIR = _REPO_ROOT / "ml" / "data" / "raw_bcs"
REPORTS_DIR = _REPO_ROOT / "ml" / "reports" / "strategies"

DEFAULT_COMMISSION_BPS = 5.0
DEFAULT_SLIPPAGE_BPS = 5.0
PROMISING_MIN_TRADES = 50
PROMISING_MIN_PROFIT_FACTOR = 1.10
PROMISING_MIN_AVG_NET_RETURN_PCT = 0.0
PROMISING_MIN_CUM_NET_RETURN_PCT = 0.0
PROMISING_MIN_ACTIVE_MONTHS = 6
PROMISING_MAX_MONTHLY_CONCENTRATION = 0.6  # one month <= 60 % of total return

# A setup must clear these floors before it is even considered as best_train.
# Setups below these floors can still appear in the grid CSV for diagnostic use
# but cannot become the primary candidate (see _select_best_train).
MIN_TRAIN_TRADES_FOR_BEST = 20
MIN_TEST_TRADES_FOR_BEST = 5
MIN_TRAIN_ACTIVE_MONTHS_FOR_BEST = 2
# Below this trade count, profit_factor=inf/999 is not meaningful and should
# be discounted when ranking candidates.
PF_RELIABILITY_MIN_TRADES = 10

GRID_ATR_LENGTHS = [5, 7, 10, 14, 20, 30, 50]
GRID_MULTIPLIERS = [1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]
DEFAULT_MODES = ["long_only", "short_only", "long_short_reversal"]
DEFAULT_TIMEFRAMES = ["M5", "M15", "H1"]

# (tp_pct, sl_pct, horizon_bars). None means "no TP/SL — exit on opposite signal".
TP_SL_OVERLAYS: list[tuple[float | None, float | None, int | None]] = [
    (None, None, None),
    (0.4, 0.25, 12),
    (0.8, 0.4, 24),
    (1.2, 0.6, 48),
]


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass
class CandleSet:
    ticker: str
    class_code: str
    timeframe: str
    df: pd.DataFrame


def _coerce_timestamp(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df = df.dropna(subset=["timestamp"])
    df = df.sort_values("timestamp").drop_duplicates("timestamp", keep="last")
    df = df.set_index("timestamp")
    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df[(df[["open", "high", "low", "close"]] > 0).all(axis=1)]
    df = df[df["high"] >= df["low"]]
    return df


def load_candles(ticker: str, class_code: str, timeframe: str) -> CandleSet | None:
    path = RAW_DIR / f"{ticker}_{class_code}_{timeframe}.csv"
    if not path.exists():
        return None
    raw = pd.read_csv(path)
    df = _coerce_timestamp(raw)
    if df.empty:
        return None
    return CandleSet(ticker=ticker, class_code=class_code, timeframe=timeframe, df=df)


_TIMEFRAME_TOKEN_RE = re.compile(r"(?<![A-Z0-9])(M5|M15|M30|H1|H4|D1|D)(?![A-Z0-9])", re.IGNORECASE)


def infer_timeframe_from_filename(path: Path) -> str:
    stem = path.stem.upper()
    matches = _TIMEFRAME_TOKEN_RE.findall(stem)
    if not matches:
        return "UNKNOWN"
    # Last match wins so prefixes like CONTINUOUS_M15 resolve to M15 rather
    # than to any earlier accidental token.
    tf = matches[-1].upper()
    return "D" if tf == "D1" else tf


def load_candles_from_path(
    path: Path,
    *,
    ticker: str | None = None,
    class_code: str | None = None,
    timeframe: str | None = None,
) -> CandleSet | None:
    """Load a generic OHLC CSV. Required columns: timestamp/open/high/low/close.

    Extra columns (volume, source_ticker, etc.) are accepted and ignored.
    """
    if not path.exists():
        return None
    raw = pd.read_csv(path)
    required = ("timestamp", "open", "high", "low", "close")
    missing = [c for c in required if c not in raw.columns]
    if missing:
        raise ValueError(
            f"CSV {path} is missing required columns: {missing}. "
            f"Expected at minimum: {required}."
        )
    df = _coerce_timestamp(raw)
    if df.empty:
        return None
    tf = timeframe or infer_timeframe_from_filename(path)
    return CandleSet(
        ticker=ticker or path.stem.upper(),
        class_code=class_code or "CSV",
        timeframe=tf,
        df=df,
    )


# ---------------------------------------------------------------------------
# Trade simulation
# ---------------------------------------------------------------------------


@dataclass
class Trade:
    entry_index: int
    exit_index: int
    side: int  # +1 long, -1 short
    entry_price: float
    exit_price: float
    gross_return_pct: float
    net_return_pct: float
    exit_reason: str
    holding_bars: int
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp


def _apply_costs(side: int, entry: float, exit_: float, commission_bps: float, slippage_bps: float) -> tuple[float, float]:
    """Return (gross_return_pct, net_return_pct) including roundtrip costs."""
    gross_pct = (exit_ - entry) / entry * 100.0 * side
    cost_pct = (commission_bps + slippage_bps) / 100.0 * 2  # both bps -> percent, applied twice
    return gross_pct, gross_pct - cost_pct


def simulate(
    df_with_signals: pd.DataFrame,
    mode: str,
    *,
    tp_pct: float | None = None,
    sl_pct: float | None = None,
    horizon: int | None = None,
    commission_bps: float = DEFAULT_COMMISSION_BPS,
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
    min_distance_pct: float | None = None,
) -> list[Trade]:
    """Walk the frame bar-by-bar and produce a list of completed trades."""
    if mode not in {"long_only", "short_only", "long_short_reversal"}:
        raise ValueError(f"Unknown backtest mode: {mode}")

    high = df_with_signals["high"].to_numpy(dtype=float)
    low = df_with_signals["low"].to_numpy(dtype=float)
    close = df_with_signals["close"].to_numpy(dtype=float)
    direction = df_with_signals["supertrend_direction"].to_numpy(dtype=np.int8)
    long_entry = df_with_signals["long_entry"].to_numpy(dtype=bool)
    short_entry = df_with_signals["short_entry"].to_numpy(dtype=bool)
    distance_pct = df_with_signals.get(
        "supertrend_distance_pct", pd.Series(np.zeros(len(df_with_signals)), index=df_with_signals.index)
    ).to_numpy(dtype=float)
    index = df_with_signals.index
    n = len(df_with_signals)

    trades: list[Trade] = []
    position = 0  # -1, 0, +1
    entry_price = math.nan
    entry_idx = -1

    def open_trade(side: int, i: int):
        nonlocal position, entry_price, entry_idx
        position = side
        entry_price = float(close[i])
        entry_idx = i

    def close_trade(i: int, exit_price: float, reason: str):
        nonlocal position, entry_price, entry_idx
        gross_pct, net_pct = _apply_costs(position, entry_price, exit_price, commission_bps, slippage_bps)
        trades.append(
            Trade(
                entry_index=entry_idx,
                exit_index=i,
                side=position,
                entry_price=entry_price,
                exit_price=float(exit_price),
                gross_return_pct=float(gross_pct),
                net_return_pct=float(net_pct),
                exit_reason=reason,
                holding_bars=i - entry_idx,
                entry_time=index[entry_idx],
                exit_time=index[i],
            )
        )
        position = 0
        entry_price = math.nan
        entry_idx = -1

    def passes_filter(side: int, i: int) -> bool:
        if min_distance_pct is None:
            return True
        d = distance_pct[i]
        if not np.isfinite(d):
            return False
        return abs(d) >= min_distance_pct

    for i in range(n):
        if position != 0:
            stopped_out = False
            if tp_pct is not None and sl_pct is not None:
                hit_sl = (low[i] <= entry_price * (1 - sl_pct / 100.0)) if position == 1 \
                    else (high[i] >= entry_price * (1 + sl_pct / 100.0))
                hit_tp = (high[i] >= entry_price * (1 + tp_pct / 100.0)) if position == 1 \
                    else (low[i] <= entry_price * (1 - tp_pct / 100.0))
                if hit_sl:
                    exit_price = entry_price * (1 - sl_pct / 100.0) if position == 1 \
                        else entry_price * (1 + sl_pct / 100.0)
                    close_trade(i, exit_price, "sl")
                    stopped_out = True
                elif hit_tp:
                    exit_price = entry_price * (1 + tp_pct / 100.0) if position == 1 \
                        else entry_price * (1 - tp_pct / 100.0)
                    close_trade(i, exit_price, "tp")
                    stopped_out = True

            if not stopped_out and horizon is not None and (i - entry_idx) >= horizon:
                close_trade(i, float(close[i]), "horizon")
                stopped_out = True

            if not stopped_out:
                # Exit on opposite signal / flip according to mode.
                if mode == "long_only" and position == 1 and short_entry[i]:
                    close_trade(i, float(close[i]), "signal_flip")
                elif mode == "short_only" and position == -1 and long_entry[i]:
                    close_trade(i, float(close[i]), "signal_flip")
                elif mode == "long_short_reversal":
                    if position == 1 and short_entry[i]:
                        close_trade(i, float(close[i]), "signal_flip")
                    elif position == -1 and long_entry[i]:
                        close_trade(i, float(close[i]), "signal_flip")

        if position == 0:
            if mode == "long_only" and long_entry[i] and passes_filter(1, i):
                open_trade(1, i)
            elif mode == "short_only" and short_entry[i] and passes_filter(-1, i):
                open_trade(-1, i)
            elif mode == "long_short_reversal":
                if long_entry[i] and passes_filter(1, i):
                    open_trade(1, i)
                elif short_entry[i] and passes_filter(-1, i):
                    open_trade(-1, i)

    # Don't force-close open positions — that would inject look-ahead via incomplete signal.
    return trades


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b not in (0, 0.0) else default


def _monthly_returns(trades: list[Trade]) -> dict[str, float]:
    if not trades:
        return {}
    months: dict[str, float] = {}
    for t in trades:
        key = t.exit_time.strftime("%Y-%m")
        months[key] = months.get(key, 0.0) + t.net_return_pct
    return months


def _compute_drawdown(equity: list[float]) -> float:
    """Max drawdown of an additive % equity curve, reported as a negative percent."""
    if not equity:
        return 0.0
    peak = equity[0]
    max_dd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        # Equity is already expressed in percentage points (additive net %).
        # Drawdown is the peak-to-trough drop in those points; no division.
        dd = v - peak
        if dd < max_dd:
            max_dd = dd
    return float(max_dd)


def compute_metrics(
    trades: list[Trade],
    candles: int,
    start_time: pd.Timestamp | None,
    end_time: pd.Timestamp | None,
) -> dict:
    metrics: dict = {
        "total_trades": len(trades),
        "long_trades": sum(1 for t in trades if t.side == 1),
        "short_trades": sum(1 for t in trades if t.side == -1),
    }
    if not trades:
        metrics.update(
            {
                "win_rate": 0.0,
                "average_gross_return_pct": 0.0,
                "average_net_return_pct": 0.0,
                "median_net_return_pct": 0.0,
                "cumulative_net_return_pct": 0.0,
                "cagr_pct": 0.0,
                "profit_factor": 0.0,
                "max_drawdown_pct": 0.0,
                "best_trade_pct": 0.0,
                "worst_trade_pct": 0.0,
                "average_holding_bars": 0.0,
                "exposure_pct": 0.0,
                "trades_per_month": 0.0,
                "long": {},
                "short": {},
                "monthly": {},
                "active_months": 0,
                "monthly_concentration": 0.0,
            }
        )
        return metrics

    nets = np.array([t.net_return_pct for t in trades])
    gross = np.array([t.gross_return_pct for t in trades])
    holding = np.array([t.holding_bars for t in trades])

    wins = nets[nets > 0]
    losses = nets[nets < 0]
    profit_factor = _safe_div(float(wins.sum()), float(-losses.sum()), default=float("inf") if wins.sum() > 0 else 0.0)
    if not np.isfinite(profit_factor):
        profit_factor = 999.0

    # Equity = cumulative net return as additive percent points (a stable proxy
    # for compounded equity that doesn't blow up on outlier trades).
    equity = [0.0]
    for net in nets:
        equity.append(equity[-1] + float(net))
    cumulative = float(equity[-1])

    # Approximate CAGR from cumulative additive return + elapsed span.
    cagr = 0.0
    if start_time is not None and end_time is not None:
        days = max((end_time - start_time).total_seconds() / 86400.0, 1.0)
        years = days / 365.25
        if years > 0:
            # Treat cumulative percent as if it were a simple-interest period return.
            cagr = cumulative / years

    exposure = float(holding.sum()) / max(candles, 1) * 100.0
    monthly = _monthly_returns(trades)
    active_months = sum(1 for v in monthly.values() if abs(v) > 1e-6)
    months_elapsed = max(len(monthly), 1)
    trades_per_month = len(trades) / months_elapsed
    total_abs_return = sum(abs(v) for v in monthly.values()) or 1.0
    max_month_share = max((abs(v) / total_abs_return) for v in monthly.values()) if monthly else 0.0

    def side_metrics(side: int) -> dict:
        sub = [t for t in trades if t.side == side]
        if not sub:
            return {"trades": 0, "win_rate": 0.0, "avg_net_return_pct": 0.0, "profit_factor": 0.0}
        sub_nets = np.array([t.net_return_pct for t in sub])
        sub_wins = sub_nets[sub_nets > 0]
        sub_losses = sub_nets[sub_nets < 0]
        return {
            "trades": len(sub),
            "win_rate": float((sub_nets > 0).mean()),
            "avg_net_return_pct": float(sub_nets.mean()),
            "profit_factor": _safe_div(float(sub_wins.sum()), float(-sub_losses.sum()),
                                       default=float(999.0 if sub_wins.sum() > 0 else 0.0)),
        }

    metrics.update(
        {
            "win_rate": float((nets > 0).mean()),
            "average_gross_return_pct": float(gross.mean()),
            "average_net_return_pct": float(nets.mean()),
            "median_net_return_pct": float(np.median(nets)),
            "cumulative_net_return_pct": cumulative,
            "cagr_pct": float(cagr),
            "profit_factor": float(profit_factor),
            "max_drawdown_pct": float(_compute_drawdown(equity)),
            "best_trade_pct": float(nets.max()),
            "worst_trade_pct": float(nets.min()),
            "average_holding_bars": float(holding.mean()),
            "exposure_pct": float(exposure),
            "trades_per_month": float(trades_per_month),
            "long": side_metrics(1),
            "short": side_metrics(-1),
            "monthly": {k: float(v) for k, v in sorted(monthly.items())},
            "active_months": int(active_months),
            "monthly_concentration": float(max_month_share),
        }
    )
    return metrics


# ---------------------------------------------------------------------------
# Promising criteria + composite score
# ---------------------------------------------------------------------------


def evaluate_promising(metrics: dict) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if metrics["total_trades"] < PROMISING_MIN_TRADES:
        reasons.append(f"total_trades<{PROMISING_MIN_TRADES}")
    if metrics["profit_factor"] < PROMISING_MIN_PROFIT_FACTOR:
        reasons.append(f"profit_factor<{PROMISING_MIN_PROFIT_FACTOR}")
    if metrics["average_net_return_pct"] <= PROMISING_MIN_AVG_NET_RETURN_PCT:
        reasons.append("avg_net_return_pct<=0")
    if metrics["cumulative_net_return_pct"] <= PROMISING_MIN_CUM_NET_RETURN_PCT:
        reasons.append("cumulative_net_return_pct<=0")
    if metrics["max_drawdown_pct"] < -50.0:
        reasons.append("max_drawdown<-50")
    if metrics["active_months"] < PROMISING_MIN_ACTIVE_MONTHS:
        reasons.append(f"active_months<{PROMISING_MIN_ACTIVE_MONTHS}")
    if metrics["monthly_concentration"] > PROMISING_MAX_MONTHLY_CONCENTRATION:
        reasons.append("monthly_concentration_high")
    return (not reasons), reasons


def overfit_penalty(metrics: dict, atr_length: int, multiplier: float) -> float:
    penalty = 0.0
    if metrics["total_trades"] < PROMISING_MIN_TRADES:
        penalty += (PROMISING_MIN_TRADES - metrics["total_trades"]) * 0.5
    if metrics["active_months"] < PROMISING_MIN_ACTIVE_MONTHS:
        penalty += (PROMISING_MIN_ACTIVE_MONTHS - metrics["active_months"]) * 2.0
    if metrics["monthly_concentration"] > PROMISING_MAX_MONTHLY_CONCENTRATION:
        penalty += (metrics["monthly_concentration"] - PROMISING_MAX_MONTHLY_CONCENTRATION) * 50.0
    if atr_length in (GRID_ATR_LENGTHS[0], GRID_ATR_LENGTHS[-1]):
        penalty += 1.0
    if multiplier in (GRID_MULTIPLIERS[0], GRID_MULTIPLIERS[-1]):
        penalty += 1.0
    return penalty


def composite_score(metrics: dict, atr_length: int, multiplier: float) -> float:
    pf = metrics.get("profit_factor", 0.0)
    total_trades = int(metrics.get("total_trades", 0))
    # When trade count is too small to make profit_factor meaningful, ignore
    # the PF contribution to the score entirely. Otherwise cap to keep the
    # scale comparable across setups.
    if total_trades < PF_RELIABILITY_MIN_TRADES or pf >= 999.0:
        pf_contribution = 0.0
    else:
        pf_contribution = (min(pf, 10.0) - 1.0) * 10.0

    score = (
        metrics.get("average_net_return_pct", 0.0) * 100.0
        + pf_contribution
        + metrics.get("win_rate", 0.0) * 2.0
        - abs(metrics.get("max_drawdown_pct", 0.0)) * 0.05
        - overfit_penalty(metrics, atr_length, multiplier)
        - _low_sample_penalty(metrics)
    )
    return float(score)


def _low_sample_penalty(metrics: dict) -> float:
    """Penalise setups with too few trades, no OOS trades, narrow time spread,
    or extreme month concentration."""
    penalty = 0.0
    total_trades = int(metrics.get("total_trades", 0))
    if total_trades < MIN_TRAIN_TRADES_FOR_BEST:
        # Big hit — keep these out of the top of the ranking.
        penalty += (MIN_TRAIN_TRADES_FOR_BEST - total_trades) * 5.0
    if total_trades == 0:
        penalty += 50.0
    active_months = int(metrics.get("active_months", 0))
    if active_months < MIN_TRAIN_ACTIVE_MONTHS_FOR_BEST:
        penalty += (MIN_TRAIN_ACTIVE_MONTHS_FOR_BEST - active_months) * 10.0
    if metrics.get("monthly_concentration", 0.0) > PROMISING_MAX_MONTHLY_CONCENTRATION:
        penalty += (metrics["monthly_concentration"] - PROMISING_MAX_MONTHLY_CONCENTRATION) * 30.0
    return float(penalty)


def is_eligible_for_best_train(train_metrics: dict, test_metrics: dict) -> bool:
    """Return True when a setup has enough train + OOS trades to be a credible
    primary candidate, not just a grid diagnostic."""
    if int(train_metrics.get("total_trades", 0)) < MIN_TRAIN_TRADES_FOR_BEST:
        return False
    if int(test_metrics.get("total_trades", 0)) < MIN_TEST_TRADES_FOR_BEST:
        return False
    if int(train_metrics.get("active_months", 0)) < MIN_TRAIN_ACTIVE_MONTHS_FOR_BEST:
        return False
    return True


# ---------------------------------------------------------------------------
# Grid search
# ---------------------------------------------------------------------------


@dataclass
class GridRow:
    timeframe: str
    mode: str
    atr_length: int
    multiplier: float
    tp_pct: float | None
    sl_pct: float | None
    horizon: int | None
    distance_filter_pct: float | None
    train_metrics: dict = field(default_factory=dict)
    test_metrics: dict = field(default_factory=dict)
    score_train: float = 0.0
    score_test: float = 0.0


def _split_train_test(df: pd.DataFrame, train_ratio: float = 0.7) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not 0 < train_ratio < 1:
        raise ValueError("train_ratio must be in (0, 1)")
    cut = int(len(df) * train_ratio)
    return df.iloc[:cut].copy(), df.iloc[cut:].copy()


def _run_one(
    df: pd.DataFrame,
    *,
    atr_length: int,
    multiplier: float,
    mode: str,
    tp_pct: float | None,
    sl_pct: float | None,
    horizon: int | None,
    distance_filter_pct: float | None,
) -> tuple[list[Trade], dict]:
    augmented = add_supertrend_signals(df, atr_length=atr_length, multiplier=multiplier)
    trades = simulate(
        augmented,
        mode=mode,
        tp_pct=tp_pct,
        sl_pct=sl_pct,
        horizon=horizon,
        min_distance_pct=distance_filter_pct,
    )
    metrics = compute_metrics(
        trades,
        candles=len(df),
        start_time=df.index[0] if len(df) else None,
        end_time=df.index[-1] if len(df) else None,
    )
    return trades, metrics


def run_grid(
    candle_sets: list[CandleSet],
    modes: list[str],
    *,
    train_ratio: float = 0.7,
    include_filtered: bool = True,
) -> list[GridRow]:
    rows: list[GridRow] = []
    for cs in candle_sets:
        if len(cs.df) < 200:
            continue
        train_df, test_df = _split_train_test(cs.df, train_ratio)
        if len(train_df) < 100 or len(test_df) < 50:
            continue
        for atr_length, multiplier, mode, overlay in itertools.product(
            GRID_ATR_LENGTHS, GRID_MULTIPLIERS, modes, TP_SL_OVERLAYS
        ):
            tp, sl, horizon = overlay
            filter_choices: list[float | None] = [None]
            if include_filtered and tp is None:
                filter_choices.append(0.25)

            for dist_filter in filter_choices:
                try:
                    _, train_metrics = _run_one(
                        train_df,
                        atr_length=atr_length,
                        multiplier=multiplier,
                        mode=mode,
                        tp_pct=tp,
                        sl_pct=sl,
                        horizon=horizon,
                        distance_filter_pct=dist_filter,
                    )
                    _, test_metrics = _run_one(
                        test_df,
                        atr_length=atr_length,
                        multiplier=multiplier,
                        mode=mode,
                        tp_pct=tp,
                        sl_pct=sl,
                        horizon=horizon,
                        distance_filter_pct=dist_filter,
                    )
                except Exception as exc:  # keep grid moving on a single failure
                    print(f"  skip ATR={atr_length} mult={multiplier} mode={mode}: {exc}")
                    continue

                rows.append(
                    GridRow(
                        timeframe=cs.timeframe,
                        mode=mode,
                        atr_length=atr_length,
                        multiplier=multiplier,
                        tp_pct=tp,
                        sl_pct=sl,
                        horizon=horizon,
                        distance_filter_pct=dist_filter,
                        train_metrics=train_metrics,
                        test_metrics=test_metrics,
                        score_train=composite_score(train_metrics, atr_length, multiplier),
                        score_test=composite_score(test_metrics, atr_length, multiplier),
                    )
                )
    return rows


# ---------------------------------------------------------------------------
# Walk-forward
# ---------------------------------------------------------------------------


def _walk_forward(
    cs: CandleSet,
    atr_length: int,
    multiplier: float,
    mode: str,
    tp: float | None,
    sl: float | None,
    horizon: int | None,
    n_folds: int = 4,
) -> list[dict]:
    fold_metrics: list[dict] = []
    n = len(cs.df)
    if n < 400:
        return fold_metrics
    train_initial = int(n * 0.4)
    remaining = n - train_initial
    fold_size = remaining // n_folds
    if fold_size < 50:
        return fold_metrics
    for k in range(n_folds):
        train_end = train_initial + k * fold_size
        test_start = train_end
        test_end = min(test_start + fold_size, n)
        if test_end - test_start < 25:
            continue
        train_slice = cs.df.iloc[:train_end].copy()
        test_slice = cs.df.iloc[test_start:test_end].copy()
        _, train_m = _run_one(
            train_slice,
            atr_length=atr_length,
            multiplier=multiplier,
            mode=mode,
            tp_pct=tp,
            sl_pct=sl,
            horizon=horizon,
            distance_filter_pct=None,
        )
        _, test_m = _run_one(
            test_slice,
            atr_length=atr_length,
            multiplier=multiplier,
            mode=mode,
            tp_pct=tp,
            sl_pct=sl,
            horizon=horizon,
            distance_filter_pct=None,
        )
        fold_metrics.append(
            {
                "fold": k + 1,
                "train_range": [
                    train_slice.index[0].isoformat() if len(train_slice) else None,
                    train_slice.index[-1].isoformat() if len(train_slice) else None,
                ],
                "test_range": [
                    test_slice.index[0].isoformat() if len(test_slice) else None,
                    test_slice.index[-1].isoformat() if len(test_slice) else None,
                ],
                "train": _compact_metrics(train_m),
                "test": _compact_metrics(test_m),
            }
        )
    return fold_metrics


def _compact_metrics(m: dict) -> dict:
    keep = [
        "total_trades", "win_rate", "average_net_return_pct",
        "cumulative_net_return_pct", "profit_factor", "max_drawdown_pct",
        "trades_per_month", "active_months",
    ]
    return {k: m.get(k) for k in keep}


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _row_to_csv_record(row: GridRow) -> dict:
    record = {
        "timeframe": row.timeframe,
        "mode": row.mode,
        "atr_length": row.atr_length,
        "multiplier": row.multiplier,
        "tp_pct": row.tp_pct if row.tp_pct is not None else "",
        "sl_pct": row.sl_pct if row.sl_pct is not None else "",
        "horizon": row.horizon if row.horizon is not None else "",
        "distance_filter_pct": row.distance_filter_pct if row.distance_filter_pct is not None else "",
        "score_train": round(row.score_train, 4),
        "score_test": round(row.score_test, 4),
    }
    for split, metrics in (("train", row.train_metrics), ("test", row.test_metrics)):
        record[f"{split}_total_trades"] = metrics.get("total_trades", 0)
        record[f"{split}_win_rate"] = round(metrics.get("win_rate", 0.0), 4)
        record[f"{split}_avg_net_return_pct"] = round(metrics.get("average_net_return_pct", 0.0), 4)
        record[f"{split}_cum_net_return_pct"] = round(metrics.get("cumulative_net_return_pct", 0.0), 4)
        record[f"{split}_profit_factor"] = round(metrics.get("profit_factor", 0.0), 4)
        record[f"{split}_max_drawdown_pct"] = round(metrics.get("max_drawdown_pct", 0.0), 4)
        record[f"{split}_active_months"] = metrics.get("active_months", 0)
    return record


def save_grid_csv(path: Path, rows: list[GridRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    records = [_row_to_csv_record(r) for r in rows]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(records[0].keys()))
        writer.writeheader()
        writer.writerows(records)


def _row_to_dict(row: GridRow) -> dict:
    return {
        "timeframe": row.timeframe,
        "mode": row.mode,
        "atr_length": row.atr_length,
        "multiplier": row.multiplier,
        "tp_pct": row.tp_pct,
        "sl_pct": row.sl_pct,
        "horizon": row.horizon,
        "distance_filter_pct": row.distance_filter_pct,
        "score_train": row.score_train,
        "score_test": row.score_test,
        "train_metrics": row.train_metrics,
        "test_metrics": row.test_metrics,
    }


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _sanity_check_candle_sets(candle_sets: list[CandleSet]) -> None:
    for cs in candle_sets:
        sample = add_supertrend_signals(cs.df, atr_length=10, multiplier=3.0)
        issues = sanity_check(sample, atr_length=10)
        if issues:
            print(f"  sanity issues on {cs.ticker}/{cs.class_code}/{cs.timeframe}: {issues}")


def _run_for_candle_sets(
    candle_sets: list[CandleSet],
    *,
    ticker: str,
    class_code: str,
    modes: list[str],
    output_prefix: str,
) -> dict:
    if not candle_sets:
        return {"error": "no_data", "ticker": ticker, "class_code": class_code}

    print(f"[backtest] running grid for {ticker}/{class_code} on {len(candle_sets)} timeframe(s)...")
    rows = run_grid(candle_sets, modes)
    print(f"[backtest] grid produced {len(rows)} setups")

    eligible_rows = [
        r for r in rows if is_eligible_for_best_train(r.train_metrics, r.test_metrics)
    ]
    best_train = max(eligible_rows, key=lambda r: r.score_train) if eligible_rows else None
    best_train_diagnostic = max(rows, key=lambda r: r.score_train) if rows else None
    best_test = max(rows, key=lambda r: r.score_test) if rows else None

    # Promising-on-OOS gating using best-train setup's OOS metrics.
    selected: dict | None = None
    rejection_reasons: list[str] = []
    low_trade_note: str | None = None
    if best_train is not None:
        ok, reasons = evaluate_promising(best_train.test_metrics)
        if ok:
            selected = _row_to_dict(best_train)
        else:
            rejection_reasons = reasons
    elif rows:
        low_trade_note = (
            "No setup had enough trades for meaningful optimization."
        )
        rejection_reasons = [
            f"no_setup_with_train_trades>={MIN_TRAIN_TRADES_FOR_BEST}",
            f"or_test_trades>={MIN_TEST_TRADES_FOR_BEST}",
            f"or_active_months>={MIN_TRAIN_ACTIVE_MONTHS_FOR_BEST}",
        ]

    walk_forward_payload: dict = {}
    if best_train is not None:
        best_cs = next(cs for cs in candle_sets if cs.timeframe == best_train.timeframe)
        fold_metrics = _walk_forward(
            best_cs,
            atr_length=best_train.atr_length,
            multiplier=best_train.multiplier,
            mode=best_train.mode,
            tp=best_train.tp_pct,
            sl=best_train.sl_pct,
            horizon=best_train.horizon,
        )
        walk_forward_payload = {
            "ticker": ticker,
            "class_code": class_code,
            "timeframe": best_train.timeframe,
            "atr_length": best_train.atr_length,
            "multiplier": best_train.multiplier,
            "mode": best_train.mode,
            "tp_pct": best_train.tp_pct,
            "sl_pct": best_train.sl_pct,
            "horizon": best_train.horizon,
            "folds": fold_metrics,
        }

    summary = {
        "ticker": ticker,
        "class_code": class_code,
        "timeframes_tested": [cs.timeframe for cs in candle_sets],
        "modes_tested": modes,
        "grid_size": len(rows),
        "eligible_grid_size": len(eligible_rows),
        "best_train": _row_to_dict(best_train) if best_train else None,
        "best_train_diagnostic_only": (
            _row_to_dict(best_train_diagnostic)
            if best_train_diagnostic and best_train is None
            else None
        ),
        "best_test_diagnostic_only": _row_to_dict(best_test) if best_test else None,
        "selected_candidate": selected,
        "promising": selected is not None,
        "rejection_reasons": rejection_reasons,
        "low_trade_note": low_trade_note,
        "recommended_default_when_not_robust": {
            "mode": "long_only",
            "atr_length": 10,
            "multiplier": 3.0,
            "tp_pct": None,
            "sl_pct": None,
            "horizon": None,
            "note": "Conservative SuperTrend defaults; not validated as profitable.",
        },
        "comparison_with_existing_signals": {
            "supertrend": "rule-based research candidate (this report)",
            "mock": "in-app demo signal, not predictive",
            "pa_short_catboost": "experimental CatBoost research model, no positive net expectancy in prior backtest",
            "third_catboost_model": "not trained in this patch",
        },
        "promising_criteria": {
            "min_trades": PROMISING_MIN_TRADES,
            "min_profit_factor": PROMISING_MIN_PROFIT_FACTOR,
            "min_avg_net_return_pct": PROMISING_MIN_AVG_NET_RETURN_PCT,
            "min_cumulative_net_return_pct": PROMISING_MIN_CUM_NET_RETURN_PCT,
            "min_active_months": PROMISING_MIN_ACTIVE_MONTHS,
            "max_monthly_concentration": PROMISING_MAX_MONTHLY_CONCENTRATION,
            "max_drawdown_floor_pct": -50.0,
        },
        "best_train_eligibility_floors": {
            "min_train_trades": MIN_TRAIN_TRADES_FOR_BEST,
            "min_test_trades": MIN_TEST_TRADES_FOR_BEST,
            "min_train_active_months": MIN_TRAIN_ACTIVE_MONTHS_FOR_BEST,
            "pf_reliability_min_trades": PF_RELIABILITY_MIN_TRADES,
        },
    }

    prefix = output_prefix or f"supertrend_{ticker.lower()}"
    summary_path = REPORTS_DIR / f"{prefix}_summary.json"
    grid_path = REPORTS_DIR / f"{prefix}_grid.csv"
    best_path = REPORTS_DIR / f"{prefix}_best.json"
    wf_path = REPORTS_DIR / f"{prefix}_walk_forward.json"

    write_json(summary_path, summary)
    save_grid_csv(grid_path, rows)
    if best_train:
        write_json(
            best_path,
            {
                "ticker": ticker,
                "class_code": class_code,
                "best_train": _row_to_dict(best_train),
                "out_of_sample_test_metrics": best_train.test_metrics,
            },
        )
    write_json(wf_path, walk_forward_payload)

    print(f"[backtest] wrote:")
    print(f"  - {summary_path.relative_to(_REPO_ROOT)}")
    print(f"  - {grid_path.relative_to(_REPO_ROOT)}")
    if best_train:
        print(f"  - {best_path.relative_to(_REPO_ROOT)}")
    print(f"  - {wf_path.relative_to(_REPO_ROOT)}")

    return summary


def run_for_ticker(
    ticker: str,
    class_code: str,
    timeframes: list[str],
    modes: list[str],
    *,
    output_prefix: str | None = None,
) -> dict:
    candle_sets: list[CandleSet] = []
    for tf in timeframes:
        cs = load_candles(ticker, class_code, tf)
        if cs is None:
            print(f"[backtest] no data for {ticker}/{class_code}/{tf} — skipping")
            continue
        candle_sets.append(cs)
    _sanity_check_candle_sets(candle_sets)
    return _run_for_candle_sets(
        candle_sets,
        ticker=ticker,
        class_code=class_code,
        modes=modes,
        output_prefix=output_prefix or f"supertrend_{ticker.lower()}",
    )


def run_for_csv_path(
    csv_path: Path,
    modes: list[str],
    *,
    output_prefix: str,
) -> dict:
    try:
        cs = load_candles_from_path(csv_path)
    except ValueError as exc:
        print(f"[backtest] cannot load CSV {csv_path}: {exc}", file=sys.stderr)
        return {"error": "bad_csv", "csv_path": str(csv_path)}
    if cs is None:
        print(f"[backtest] CSV not found or empty: {csv_path}", file=sys.stderr)
        return {"error": "no_data", "csv_path": str(csv_path)}
    print(
        f"[backtest] csv-path mode: {csv_path.name} rows={len(cs.df)} "
        f"timeframe={cs.timeframe}"
    )
    _sanity_check_candle_sets([cs])
    return _run_for_candle_sets(
        [cs],
        ticker=cs.ticker,
        class_code=cs.class_code,
        modes=modes,
        output_prefix=output_prefix,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Backtest the SuperTrend rule-based strategy.")
    p.add_argument("--ticker", default="GOLD")
    p.add_argument("--class-code", default="FEG")
    p.add_argument("--timeframes", nargs="+", default=DEFAULT_TIMEFRAMES,
                   help="Timeframes to backtest (default: M5 M15 H1).")
    p.add_argument("--modes", nargs="+", default=DEFAULT_MODES,
                   choices=["long_only", "short_only", "long_short_reversal"],
                   help="Backtest modes (default: all three).")
    p.add_argument("--all", action="store_true",
                   help="Convenience flag — run all timeframes + modes (current default).")
    p.add_argument(
        "--csv-path",
        default=None,
        help=(
            "Optional path to an OHLC CSV (e.g. a continuous futures series). "
            "If provided, --ticker/--class-code/--timeframes are ignored and the "
            "grid runs against this file only."
        ),
    )
    p.add_argument(
        "--output-prefix",
        default=None,
        help="Report filename prefix. Required when --csv-path is used.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_argparser().parse_args(argv)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    if args.csv_path:
        if not args.output_prefix:
            print(
                "--output-prefix is required when --csv-path is provided.",
                file=sys.stderr,
            )
            return 2
        summary = run_for_csv_path(
            Path(args.csv_path),
            modes=args.modes,
            output_prefix=args.output_prefix,
        )
        if summary.get("error") in ("no_data", "bad_csv"):
            return 4
        label = Path(args.csv_path).name
    else:
        summary = run_for_ticker(
            ticker=args.ticker.upper(),
            class_code=args.class_code.upper(),
            timeframes=args.timeframes,
            modes=args.modes,
            output_prefix=args.output_prefix,
        )
        if summary.get("error") == "no_data":
            print("Run download_bcs_goods_history.py first to populate ml/data/raw_bcs/", file=sys.stderr)
            return 4
        label = f"{args.ticker}/{args.class_code}"

    if summary.get("promising"):
        print(f"[backtest] PROMISING candidate selected for {label}")
    else:
        reasons = summary.get("rejection_reasons")
        note = summary.get("low_trade_note")
        if note:
            print(f"[backtest] No promising candidate for {label}: {note}")
        else:
            print(f"[backtest] No promising candidate for {label}. Reasons: {reasons}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
