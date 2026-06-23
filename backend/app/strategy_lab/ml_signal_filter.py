"""ML signal filter for Strategy Lab v1.5 (dataset + CatBoost filter).

The ML layer here does **one thing only**: for each already-existing rule-based
*long* signal it predicts whether the signal should be taken. It never predicts
price and never generates trades -- the rule-based finalists (D: H4 SuperTrend,
C: H1 Donchian breakout) still produce every candidate trade; CatBoost only
keeps or drops them.

Pipeline (all leakage-safe -- see the per-section comments):

    1. Build a dataset: one row per *executed* rule-based trade candidate, with
       features taken at the signal candle and the realised trade outcome from
       :mod:`risk_backtester` joined on as targets.
    2. Time-split into train / validation / test by ``signal_time``.
    3. Train CatBoost on the train period to predict ``y_good_trade``.
    4. Choose a probability threshold on the *validation* period only.
    5. Apply the threshold to the held-out test period and compare the filtered
       trades against the unfiltered (original) trades.

This module is pandas/numpy + CatBoost only and never mutates MT5 exports.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd

try:  # package import
    from . import data_loader, indicators, ml_features, risk_backtester, strategies
    from .risk_backtester import RiskConfig
except ImportError:  # script import
    import data_loader  # type: ignore[no-redef]
    import indicators  # type: ignore[no-redef]
    import ml_features  # type: ignore[no-redef]
    import risk_backtester  # type: ignore[no-redef]
    import strategies  # type: ignore[no-redef]
    from risk_backtester import RiskConfig  # type: ignore[no-redef]


# ---------------------------------------------------------------------------
# Account / cost / split constants (kept consistent with v1.4 confirmation).
# ---------------------------------------------------------------------------
DEFAULT_LEVERAGE = 50.0
INITIAL_EQUITY = 10000.0
DONCHIAN_ATR_PERIOD = 14

ACCOUNT_DEFAULTS: dict = dict(
    account_currency="USD",
    contract_size=100.0,
    point_value=0.01,
)

# Base / Conservative / Stress execution-cost knobs (identical to v1.4).
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

TIMEFRAME_FILES: dict[str, str] = {
    "H1": "XAUUSDrfd_H1.csv",
    "H4": "XAUUSDrfd_H4.csv",
    "D1": "XAUUSDrfd_D1.csv",
}

# Time-based splits (by signal_time). Bounds are tz-naive UTC to match the
# backtester's timestamps. Train/validation/test never overlap.
TRAIN_RANGE = (pd.Timestamp("2015-01-01"), pd.Timestamp("2021-12-31 23:59:59"))
VALIDATION_RANGE = (pd.Timestamp("2022-01-01"), pd.Timestamp("2024-12-31 23:59:59"))
TEST_RANGE = (pd.Timestamp("2025-01-01"), None)

WALK_FORWARD_WINDOWS: tuple[tuple[str, int, int], ...] = (
    ("wf_2015_2018", 2015, 2018),
    ("wf_2019_2021", 2019, 2021),
    ("wf_2022_2024", 2022, 2024),
    ("wf_2025_2026", 2025, 2026),
)

# CatBoost hyper-parameters suggested by the v1.5 spec.
CATBOOST_PARAMS: dict = dict(
    loss_function="Logloss",
    eval_metric="AUC",
    iterations=500,
    learning_rate=0.03,
    depth=5,
    l2_leaf_reg=10,
    random_seed=42,
    early_stopping_rounds=50,
    verbose=100,
    allow_writing_files=False,
    auto_class_weights="Balanced",
)

# Threshold grid scanned on validation (inclusive of 0.90).
THRESHOLDS: np.ndarray = np.round(np.arange(0.50, 0.9001, 0.02), 2)

# Dataset column groups (identity / params / outcomes / targets are *never*
# fed to the model -- only feature columns are).
IDENTITY_COLUMNS: tuple[str, ...] = (
    "signal_id",
    "finalist",
    "strategy_label",
    "symbol",
    "timeframe",
    "signal_time",
    "entry_time",
    "direction",
    "cost_scenario",
    "leverage",
    "sizing_mode",
    "risk_percent",
)
PARAM_COLUMNS: tuple[str, ...] = (
    "atr_period",
    "multiplier",
    "initial_stop_loss_atr",
    "trailing_stop_atr",
    "take_profit_atr",
    "lookback",
    "stop_loss_atr",
)
OUTCOME_COLUMNS: tuple[str, ...] = (
    "exit_time",
    "exit_reason",
    "bars_held",
    "net_pnl",
    "r_multiple",
    "return_pct_on_equity",
    "max_floating_profit",
    "max_floating_loss",
    "is_profitable",
    "is_good_trade",
)
TARGET_COLUMNS: tuple[str, ...] = ("y_profitable", "y_good_trade")

_SIGNAL_FUNCS = {
    "donchian": strategies.donchian_breakout_strategy,
    "supertrend": strategies.supertrend_strategy,
}


def _fmt(value: object) -> str:
    """Compact, stable string for a config-id fragment (``None`` -> ``none``)."""
    if value is None:
        return "none"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


# ---------------------------------------------------------------------------
# Finalist variant definitions (dataset design, spec section 6)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class VariantConfig:
    """One rule-based finalist configuration to backtest for the dataset."""

    finalist: str
    timeframe: str
    family: str
    strategy_label: str
    direction: str
    exit_mode: str
    sizing_mode: str
    config_atr_period: int
    risk_percent: float
    leverage: float
    signal_params: dict
    exit_params: dict
    param_cols: dict  # flat strategy-parameter columns for the dataset row
    base_config_id: str  # identity without the cost scenario

    @property
    def signal_key(self) -> tuple:
        """Cache key for the (timeframe, family, strategy params) signal set."""
        return (self.timeframe, self.family, tuple(sorted(self.signal_params.items())))


# Default grids for the dataset (spec section 6).
D_GRID: dict[str, list] = {
    "atr_period": [10, 14],
    "multiplier": [2.0, 2.5],
    "initial_stop_loss_atr": [2.5, 3.0],
    "trailing_stop_atr": [5.0, 6.0, 7.0],
    "take_profit_atr": [None, 20.0, 24.0],
    "risk_percent": [0.5, 1.0],
}
C_GRID: dict[str, list] = {
    "lookback": [40, 55],
    "stop_loss_atr": [2.5, 3.0],
    "take_profit_atr": [12.0, 16.0, 20.0, 24.0],
    "risk_percent": [0.5, 1.0],
}


def _empty_param_cols() -> dict:
    return {k: np.nan for k in PARAM_COLUMNS}


def build_variants(
    finalist: str,
    leverage: float,
    *,
    max_configs: Optional[int] = None,
) -> list[VariantConfig]:
    """Enumerate the rule-based configurations for one finalist (deterministic)."""
    variants: list[VariantConfig] = []
    if finalist == "D":
        keys = list(D_GRID.keys())
        for combo in itertools.product(*(D_GRID[k] for k in keys)):
            c = dict(zip(keys, combo))
            label = f"supertrend_{c['atr_period']}_{c['multiplier']:g}"
            tp = c["take_profit_atr"]
            params = _empty_param_cols()
            params.update(
                atr_period=c["atr_period"],
                multiplier=c["multiplier"],
                initial_stop_loss_atr=c["initial_stop_loss_atr"],
                trailing_stop_atr=c["trailing_stop_atr"],
                take_profit_atr=tp if tp is not None else np.nan,
            )
            base_id = (
                f"D|H4|st{c['atr_period']}_{c['multiplier']:g}|"
                f"trail_isl{c['initial_stop_loss_atr']:g}_ts{c['trailing_stop_atr']:g}"
                f"_tp{_fmt(tp)}|risk{c['risk_percent']:g}|lev{_fmt(leverage)}"
            )
            variants.append(
                VariantConfig(
                    finalist="D",
                    timeframe="H4",
                    family="supertrend",
                    strategy_label=label,
                    direction="long_only",
                    exit_mode="atr_trailing",
                    sizing_mode="risk_percent",
                    config_atr_period=int(c["atr_period"]),
                    risk_percent=float(c["risk_percent"]),
                    leverage=leverage,
                    signal_params={
                        "atr_period": c["atr_period"],
                        "multiplier": c["multiplier"],
                    },
                    exit_params={
                        "initial_stop_loss_atr": c["initial_stop_loss_atr"],
                        "trailing_stop_atr": c["trailing_stop_atr"],
                        "take_profit_atr": tp,
                    },
                    param_cols=params,
                    base_config_id=base_id,
                )
            )
    elif finalist == "C":
        keys = list(C_GRID.keys())
        for combo in itertools.product(*(C_GRID[k] for k in keys)):
            c = dict(zip(keys, combo))
            label = f"donchian_{c['lookback']}"
            params = _empty_param_cols()
            params.update(
                lookback=c["lookback"],
                stop_loss_atr=c["stop_loss_atr"],
                take_profit_atr=c["take_profit_atr"],
            )
            base_id = (
                f"C|H1|dc{c['lookback']}|"
                f"fixed_sl{c['stop_loss_atr']:g}_tp{c['take_profit_atr']:g}|"
                f"risk{c['risk_percent']:g}|lev{_fmt(leverage)}"
            )
            variants.append(
                VariantConfig(
                    finalist="C",
                    timeframe="H1",
                    family="donchian",
                    strategy_label=label,
                    direction="long_only",
                    exit_mode="fixed_atr",
                    sizing_mode="risk_percent",
                    config_atr_period=DONCHIAN_ATR_PERIOD,
                    risk_percent=float(c["risk_percent"]),
                    leverage=leverage,
                    signal_params={"lookback": c["lookback"]},
                    exit_params={
                        "stop_loss_atr": c["stop_loss_atr"],
                        "take_profit_atr": c["take_profit_atr"],
                    },
                    param_cols=params,
                    base_config_id=base_id,
                )
            )
    else:
        raise ValueError(f"Unknown finalist {finalist!r} (expected 'D' or 'C')")

    if max_configs is not None:
        variants = variants[:max_configs]
    return variants


def _make_risk_config(variant: VariantConfig, cost_scenario: str) -> RiskConfig:
    """Build the :class:`RiskConfig` for one variant under one cost scenario."""
    kwargs = dict(
        ACCOUNT_DEFAULTS,
        initial_equity=INITIAL_EQUITY,
        leverage=variant.leverage,
        atr_period=variant.config_atr_period,
        direction_mode=variant.direction,
        exit_mode=variant.exit_mode,
        sizing_mode=variant.sizing_mode,
        risk_percent=variant.risk_percent,
    )
    kwargs.update(COST_SCENARIOS[cost_scenario])
    kwargs.update(variant.exit_params)
    return RiskConfig(**kwargs)


# ---------------------------------------------------------------------------
# Data / feature caches
# ---------------------------------------------------------------------------
@dataclass
class _TimeframeCache:
    """Loaded OHLC, single-TF features and a naive-time position index per TF."""

    df: pd.DataFrame
    features: pd.DataFrame
    naive_index: pd.DatetimeIndex
    htf_table: pd.DataFrame  # the HTF subset of this TF's features
    atr_by_period: dict[int, np.ndarray] = field(default_factory=dict)


def _load_timeframe(data_dir: Path, timeframe: str) -> _TimeframeCache:
    """Load one timeframe and precompute its features (read-only on the CSV)."""
    df = data_loader.load_mt5_csv(data_dir / TIMEFRAME_FILES[timeframe])
    features = ml_features.compute_features(df)
    naive_index = pd.DatetimeIndex(ml_features.to_naive_utc(df["datetime"]))
    htf_table = features[["datetime", *ml_features.HTF_FEATURE_SUBSET]].copy()
    return _TimeframeCache(
        df=df, features=features, naive_index=naive_index, htf_table=htf_table
    )


def _required_timeframes(finalists: Iterable[str]) -> set[str]:
    """Timeframes that must be loaded (signal TF + its higher-TF context)."""
    needed: set[str] = set()
    for f in finalists:
        if f == "D":
            needed |= {"H4", "D1"}
        elif f == "C":
            needed |= {"H1", "H4", "D1"}
    return needed


# ---------------------------------------------------------------------------
# Dataset builder (spec section 6)
# ---------------------------------------------------------------------------

def _trade_rows(
    trades: pd.DataFrame,
    *,
    variant: VariantConfig,
    cost_scenario: str,
    cache: _TimeframeCache,
    symbol: Optional[str],
) -> pd.DataFrame:
    """Turn one backtest's trades into dataset rows with signal-time features.

    No-leakage core: each trade entered at ``entry_idx = signal_idx + 1``. We
    look up ``entry_time`` to recover ``entry_idx`` and take the feature row at
    ``signal_idx`` -- the last completed bar before entry. Entry-candle OHLC is
    never read.
    """
    entry_times = pd.to_datetime(trades["entry_time"])
    entry_idx = cache.naive_index.get_indexer(entry_times)
    if (entry_idx < 0).any():  # pragma: no cover - defensive
        raise RuntimeError("Trade entry_time not found in source bars (alignment bug)")
    signal_idx = entry_idx - 1  # entry is next-bar open -> signal is the prior bar

    feats = cache.features.iloc[signal_idx].reset_index(drop=True)
    signal_time = pd.to_datetime(feats["datetime"])
    feature_cols = list(ml_features.SINGLE_TF_FEATURES)

    net_pnl = trades["net_pnl"].to_numpy(dtype=float)
    r_multiple = trades["r_multiple"].to_numpy(dtype=float)
    is_profitable = (net_pnl > 0).astype(int)
    is_good_trade = (r_multiple >= 0.5).astype(int)

    signal_time_iso = signal_time.dt.strftime("%Y-%m-%dT%H:%M:%S")
    signal_id = (
        f"{variant.base_config_id}|{cost_scenario}@" + signal_time_iso
    )

    row = {
        # identity
        "signal_id": signal_id.to_numpy(),
        "finalist": variant.finalist,
        "strategy_label": variant.strategy_label,
        "symbol": symbol,
        "timeframe": variant.timeframe,
        "signal_time": signal_time.to_numpy(),
        "entry_time": entry_times.to_numpy(),
        "direction": variant.direction,
        "cost_scenario": cost_scenario,
        "leverage": variant.leverage,
        "sizing_mode": variant.sizing_mode,
        "risk_percent": variant.risk_percent,
    }
    for key in PARAM_COLUMNS:
        row[key] = variant.param_cols[key]
    # outcomes (joined on -- never used as features)
    row.update(
        {
            "exit_time": pd.to_datetime(trades["exit_time"]).to_numpy(),
            "exit_reason": trades["exit_reason"].to_numpy(),
            "bars_held": trades["bars_held"].to_numpy(),
            "net_pnl": net_pnl,
            "r_multiple": r_multiple,
            "return_pct_on_equity": trades["return_pct_on_equity"].to_numpy(dtype=float),
            "max_floating_profit": trades["max_floating_profit"].to_numpy(dtype=float),
            "max_floating_loss": trades["max_floating_loss"].to_numpy(dtype=float),
            "is_profitable": is_profitable,
            "is_good_trade": is_good_trade,
            "y_profitable": is_profitable,
            "y_good_trade": is_good_trade,
        }
    )
    out = pd.DataFrame(row)
    for col in feature_cols:
        out[col] = feats[col].to_numpy()
    return out


def build_dataset(
    data_dir: Path,
    finalists: Iterable[str],
    cost_scenarios: Iterable[str],
    *,
    leverage: float = DEFAULT_LEVERAGE,
    max_configs: Optional[int] = None,
    verbose: bool = True,
) -> pd.DataFrame:
    """Build the full ML dataset: one row per executed rule-based trade candidate.

    For every finalist variant x cost scenario the rule-based signals are run
    through :mod:`risk_backtester`; each executed trade becomes a row carrying
    signal-time features and the realised outcome. Higher-timeframe context is
    merged with the conservative ``asof`` rule in :mod:`ml_features`.
    """
    finalists = list(finalists)
    cost_scenarios = list(cost_scenarios)
    caches: dict[str, _TimeframeCache] = {}
    for tf in sorted(_required_timeframes(finalists)):
        if verbose:
            print(f"Loading + featurising {tf} ...")
        caches[tf] = _load_timeframe(data_dir, tf)

    symbol: Optional[str] = None
    for cache in caches.values():
        if len(cache.df):
            symbol = cache.df["symbol"].iloc[0]
            break

    signal_cache: dict[tuple, pd.DataFrame] = {}
    frames: list[pd.DataFrame] = []

    for finalist in finalists:
        variants = build_variants(finalist, leverage, max_configs=max_configs)
        if verbose:
            print(
                f"Finalist {finalist}: {len(variants)} configs x "
                f"{len(cost_scenarios)} cost scenarios"
            )
        for v_i, variant in enumerate(variants, start=1):
            cache = caches[variant.timeframe]

            # Signals depend only on strategy params (not exit/cost) -> cache.
            if variant.signal_key not in signal_cache:
                signal_cache[variant.signal_key] = _SIGNAL_FUNCS[variant.family](
                    cache.df, **variant.signal_params
                )
            signals = signal_cache[variant.signal_key]

            if variant.config_atr_period not in cache.atr_by_period:
                cache.atr_by_period[variant.config_atr_period] = indicators.atr(
                    cache.df, variant.config_atr_period
                ).to_numpy(dtype=float)
            atr_values = cache.atr_by_period[variant.config_atr_period]

            for cost_scenario in cost_scenarios:
                config = _make_risk_config(variant, cost_scenario)
                trades, _equity, _skipped = risk_backtester.run_risk_backtest(
                    cache.df,
                    signals,
                    config,
                    strategy_name=variant.strategy_label,
                    symbol=symbol,
                    timeframe=variant.timeframe,
                    atr_values=atr_values,
                )
                if len(trades) == 0:
                    continue
                frames.append(
                    _trade_rows(
                        trades,
                        variant=variant,
                        cost_scenario=cost_scenario,
                        cache=cache,
                        symbol=symbol,
                    )
                )
            if verbose and v_i % 25 == 0:
                print(f"  ... {v_i}/{len(variants)} configs")

    if not frames:
        return pd.DataFrame()

    dataset = pd.concat(frames, ignore_index=True)
    dataset = _attach_htf_features(dataset, caches, verbose=verbose)
    dataset["signal_time"] = pd.to_datetime(dataset["signal_time"])
    dataset["entry_time"] = pd.to_datetime(dataset["entry_time"])
    return dataset


def _attach_htf_features(
    dataset: pd.DataFrame,
    caches: dict[str, _TimeframeCache],
    *,
    verbose: bool = True,
) -> pd.DataFrame:
    """Merge higher-timeframe context per finalist (D: D1; C: H4 + D1)."""
    parts: list[pd.DataFrame] = []
    for finalist, group in dataset.groupby("finalist", sort=False):
        group = group.reset_index(drop=True)
        if finalist == "D":
            merged = ml_features.merge_htf_features(
                group,
                caches["D1"].htf_table,
                suffix="d1",
                htf_duration=ml_features.timeframe_duration("D1"),
            )
        elif finalist == "C":
            merged = ml_features.merge_htf_features(
                group,
                caches["H4"].htf_table,
                suffix="h4",
                htf_duration=ml_features.timeframe_duration("H4"),
            )
            merged = ml_features.merge_htf_features(
                merged,
                caches["D1"].htf_table,
                suffix="d1",
                htf_duration=ml_features.timeframe_duration("D1"),
            )
        else:  # pragma: no cover - defensive
            merged = group
        parts.append(merged)
        if verbose:
            print(f"Merged higher-timeframe features for finalist {finalist}")
    # Union of columns (C has _h4 columns D lacks) -> outer concat fills NaN.
    return pd.concat(parts, ignore_index=True)


# ---------------------------------------------------------------------------
# Splits (spec section 7)
# ---------------------------------------------------------------------------

def _mask_range(
    signal_time: pd.Series, start: Optional[pd.Timestamp], end: Optional[pd.Timestamp]
) -> pd.Series:
    mask = pd.Series(True, index=signal_time.index)
    if start is not None:
        mask &= signal_time >= start
    if end is not None:
        mask &= signal_time <= end
    return mask


def assign_split(signal_time: pd.Series) -> pd.Series:
    """Label each row 'train' / 'validation' / 'test' by ``signal_time``."""
    labels = pd.Series("", index=signal_time.index, dtype=object)
    labels[_mask_range(signal_time, *TRAIN_RANGE)] = "train"
    labels[_mask_range(signal_time, *VALIDATION_RANGE)] = "validation"
    labels[_mask_range(signal_time, *TEST_RANGE)] = "test"
    return labels


def split_summary(dataset: pd.DataFrame) -> pd.DataFrame:
    """Per (finalist, cost_scenario, split) row counts and time bounds."""
    rows: list[dict] = []
    for (fin, cost, split), grp in dataset.groupby(
        ["finalist", "cost_scenario", "split"], sort=False
    ):
        rows.append(
            {
                "finalist": fin,
                "cost_scenario": cost,
                "split": split,
                "rows": int(len(grp)),
                "min_signal_time": grp["signal_time"].min(),
                "max_signal_time": grp["signal_time"].max(),
                "positive_rate_good_trade": float(grp["y_good_trade"].mean()),
                "positive_rate_profitable": float(grp["y_profitable"].mean()),
            }
        )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Pooled trade metrics (used identically for original vs filtered)
# ---------------------------------------------------------------------------

def pooled_metrics(trades: pd.DataFrame, *, initial_equity: float = INITIAL_EQUITY) -> dict:
    """Pooled performance of a *set* of selected trade candidates.

    The selected candidates come from many overlapping configs, so this is a
    pooled approximation (not a single tradable account): trades are ordered by
    ``entry_time`` and ``net_pnl`` is accumulated to derive a percentage
    drawdown. Original and filtered metrics use this same function on the same
    candidate universe, so the comparison is apples-to-apples.
    """
    n = int(len(trades))
    if n == 0:
        return {
            "trades": 0,
            "net_pnl": 0.0,
            "profit_factor": 0.0,
            "average_r": np.nan,
            "median_r": np.nan,
            "win_rate": 0.0,
            "max_drawdown_pct": 0.0,
        }
    net = trades["net_pnl"].astype(float)
    r = trades["r_multiple"].astype(float)
    gross_profit = float(net[net > 0].sum())
    gross_loss = float(-net[net < 0].sum())
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    else:
        profit_factor = float("inf") if gross_profit > 0 else 0.0

    ordered = trades.sort_values("entry_time")
    equity = initial_equity + ordered["net_pnl"].astype(float).cumsum()
    peak = equity.cummax()
    drawdown_pct = ((peak - equity) / peak * 100.0)
    max_dd_pct = float(drawdown_pct.max()) if len(drawdown_pct) else 0.0

    return {
        "trades": n,
        "net_pnl": float(net.sum()),
        "profit_factor": profit_factor,
        "average_r": float(r.mean()),
        "median_r": float(r.median()),
        "win_rate": float((net > 0).mean()),
        "max_drawdown_pct": max_dd_pct,
    }


def _clip_pf(profit_factor: float, cap: float = 100.0) -> float:
    """Finite, score-friendly profit factor (``inf`` -> ``cap``)."""
    if not np.isfinite(profit_factor):
        return cap
    return min(profit_factor, cap)


# ---------------------------------------------------------------------------
# CatBoost (lazy import so dataset-only runs need no CatBoost)
# ---------------------------------------------------------------------------

def _require_catboost():
    """Import CatBoost or fail with the exact install hint (no silent fallback)."""
    try:
        from catboost import CatBoostClassifier
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise SystemExit(
            "CatBoost is required for the ML signal filter but is not installed.\n"
            "Install the ML extras and re-run:\n\n"
            "    pip install catboost scikit-learn joblib\n\n"
            "(CatBoost is not silently replaced with another model.)"
        ) from exc
    return CatBoostClassifier


def _roc_auc(y_true: np.ndarray, y_prob: np.ndarray) -> float:
    """ROC-AUC if both classes are present, else NaN (no sklearn hard dep here)."""
    if len(np.unique(y_true)) < 2:
        return float("nan")
    try:
        from sklearn.metrics import roc_auc_score

        return float(roc_auc_score(y_true, y_prob))
    except ImportError:  # pragma: no cover
        return float("nan")


@dataclass
class TrainedModel:
    """A fitted CatBoost model plus the metadata needed to reuse it."""

    finalist: str
    cost_scenario: str
    target: str
    feature_columns: list[str]
    model: object
    train_rows: int
    validation_rows: int
    test_rows: int
    train_auc: float
    validation_auc: float
    best_iteration: int


def train_model(
    frame: pd.DataFrame,
    feature_columns: list[str],
    *,
    finalist: str,
    cost_scenario: str,
    target: str,
) -> Optional[TrainedModel]:
    """Train one CatBoost classifier on the train split (early-stop on validation).

    Returns ``None`` (with a printed reason) when a split is empty or single-class
    so the caller can skip this (finalist, cost_scenario) gracefully.
    """
    CatBoostClassifier = _require_catboost()

    work = frame.dropna(subset=[target]).copy()
    train = work[work["split"] == "train"]
    val = work[work["split"] == "validation"]
    test = work[work["split"] == "test"]

    if len(train) == 0 or len(val) == 0:
        print(
            f"  [skip] {finalist}/{cost_scenario}: empty train ({len(train)}) "
            f"or validation ({len(val)}) split"
        )
        return None
    if train[target].nunique() < 2:
        print(
            f"  [skip] {finalist}/{cost_scenario}: train target is single-class"
        )
        return None

    x_train = train[feature_columns]
    y_train = train[target].astype(int).to_numpy()
    x_val = val[feature_columns]
    y_val = val[target].astype(int).to_numpy()

    model = CatBoostClassifier(**CATBOOST_PARAMS)
    eval_set = (x_val, y_val) if val[target].nunique() >= 2 else None
    model.fit(x_train, y_train, eval_set=eval_set)

    train_prob = model.predict_proba(x_train)[:, 1]
    val_prob = model.predict_proba(x_val)[:, 1]
    return TrainedModel(
        finalist=finalist,
        cost_scenario=cost_scenario,
        target=target,
        feature_columns=feature_columns,
        model=model,
        train_rows=int(len(train)),
        validation_rows=int(len(val)),
        test_rows=int(len(test)),
        train_auc=_roc_auc(y_train, train_prob),
        validation_auc=_roc_auc(y_val, val_prob),
        best_iteration=int(getattr(model, "best_iteration_", 0) or 0),
    )


def predict_proba(trained: TrainedModel, frame: pd.DataFrame) -> np.ndarray:
    """Positive-class probabilities for ``frame`` using a trained model."""
    if len(frame) == 0:
        return np.empty(0, dtype=float)
    return trained.model.predict_proba(frame[trained.feature_columns])[:, 1]


# ---------------------------------------------------------------------------
# Threshold search (spec section 9) -- VALIDATION ONLY
# ---------------------------------------------------------------------------

@dataclass
class ThresholdChoice:
    threshold: float
    validation_score: float
    status: str  # "ok" | "weak_threshold"
    table: pd.DataFrame


def search_threshold(
    val_frame: pd.DataFrame,
    val_prob: np.ndarray,
    *,
    finalist: str,
    cost_scenario: str,
) -> ThresholdChoice:
    """Evaluate the threshold grid on validation and pick the best.

    The test set is *never* touched here. Constraints: selected_trades >= 30,
    profit_factor > 1.1, average_r > 0. If none pass, the highest-scoring
    threshold is returned with status ``weak_threshold``.
    """
    val = val_frame.copy()
    val["prob"] = val_prob
    rows: list[dict] = []
    for threshold in THRESHOLDS:
        selected = val[val["prob"] >= threshold]
        m = pooled_metrics(selected)
        selection_rate = float(len(selected) / len(val)) if len(val) else 0.0
        score = (
            _clip_pf(m["profit_factor"]) * 20.0
            + (0.0 if np.isnan(m["average_r"]) else m["average_r"]) * 20.0
            + m["trades"] / 20.0
            - m["max_drawdown_pct"]
        )
        passes = (
            m["trades"] >= 30
            and m["profit_factor"] > 1.1
            and (not np.isnan(m["average_r"]) and m["average_r"] > 0)
        )
        rows.append(
            {
                "finalist": finalist,
                "cost_scenario": cost_scenario,
                "threshold": float(threshold),
                "selected_trades": m["trades"],
                "selection_rate": selection_rate,
                "win_rate": m["win_rate"],
                "average_r": m["average_r"],
                "median_r": m["median_r"],
                "net_pnl": m["net_pnl"],
                "profit_factor": m["profit_factor"],
                "max_drawdown_pct": m["max_drawdown_pct"],
                "average_predicted_probability": (
                    float(selected["prob"].mean()) if len(selected) else np.nan
                ),
                "validation_score": float(score),
                "passes_constraints": bool(passes),
            }
        )
    table = pd.DataFrame(rows)

    # A threshold that selects *no* trades is not a usable filter (and scores a
    # misleading 0.0). Restrict the choice to thresholds that actually select
    # trades, and break score ties deterministically toward the lower (more
    # inclusive) threshold so the result is reproducible.
    usable = table[table["selected_trades"] > 0]
    if usable.empty:  # pragma: no cover - 0.50 always selects something
        usable = table
    passing = usable[usable["passes_constraints"]]
    pool = passing if len(passing) else usable
    status = "ok" if len(passing) else "weak_threshold"
    best = pool.sort_values(
        ["validation_score", "threshold"], ascending=[False, True]
    ).iloc[0]
    return ThresholdChoice(
        threshold=float(best["threshold"]),
        validation_score=float(best["validation_score"]),
        status=status,
        table=table,
    )


# ---------------------------------------------------------------------------
# Filtered backtest + walk-forward (spec sections 10, 11)
# ---------------------------------------------------------------------------

def filtered_backtest_row(
    test_frame: pd.DataFrame,
    test_prob: np.ndarray,
    *,
    finalist: str,
    cost_scenario: str,
    threshold: float,
    validation_score: float,
    test_status: str,
) -> dict:
    """Compare original vs filtered trades on the *test* period only.

    ``original`` is the full test candidate universe; ``filtered`` keeps only
    candidates with ``prob >= threshold``. The threshold was chosen on
    validation, so the test result is genuinely out-of-sample.
    """
    test = test_frame.copy()
    test["prob"] = test_prob
    original = pooled_metrics(test)
    filtered = pooled_metrics(test[test["prob"] >= threshold])
    selection_rate = (
        float(filtered["trades"] / original["trades"]) if original["trades"] else 0.0
    )
    return {
        "finalist": finalist,
        "cost_scenario": cost_scenario,
        "threshold": threshold,
        "validation_score": validation_score,
        "test_status": test_status,
        "original_trades": original["trades"],
        "filtered_trades": filtered["trades"],
        "selection_rate": selection_rate,
        "original_net_pnl": original["net_pnl"],
        "filtered_net_pnl": filtered["net_pnl"],
        "original_profit_factor": original["profit_factor"],
        "filtered_profit_factor": filtered["profit_factor"],
        "original_average_r": original["average_r"],
        "filtered_average_r": filtered["average_r"],
        "original_win_rate": original["win_rate"],
        "filtered_win_rate": filtered["win_rate"],
        "original_max_drawdown_pct": original["max_drawdown_pct"],
        "filtered_max_drawdown_pct": filtered["max_drawdown_pct"],
    }


def walk_forward_rows(
    frame: pd.DataFrame,
    trained: TrainedModel,
    *,
    threshold: float,
    test_status: str,
) -> list[dict]:
    """Original vs filtered metrics in each fixed walk-forward window.

    The model and threshold are fixed (trained on the train period, threshold
    chosen on validation); each window is scored with them. Windows inside the
    train/validation period are flagged ``in_sample`` so they are read with care.
    """
    work = frame.dropna(subset=[trained.target]).copy()
    rows: list[dict] = []
    for label, lo, hi in WALK_FORWARD_WINDOWS:
        start = pd.Timestamp(f"{lo}-01-01")
        end = pd.Timestamp(f"{hi}-12-31 23:59:59")
        window = work[_mask_range(work["signal_time"], start, end)]
        if len(window) == 0:
            continue
        prob = predict_proba(trained, window)
        win = window.copy()
        win["prob"] = prob
        original = pooled_metrics(win)
        filtered = pooled_metrics(win[win["prob"] >= threshold])
        # In-sample if the window overlaps the train+validation period at all.
        in_sample = start <= VALIDATION_RANGE[1]
        rows.append(
            {
                "finalist": trained.finalist,
                "cost_scenario": trained.cost_scenario,
                "window": label,
                "in_sample": bool(in_sample),
                "threshold": threshold,
                "test_status": test_status,
                "original_trades": original["trades"],
                "filtered_trades": filtered["trades"],
                "original_profit_factor": original["profit_factor"],
                "filtered_profit_factor": filtered["profit_factor"],
                "original_average_r": original["average_r"],
                "filtered_average_r": filtered["average_r"],
                "original_net_pnl": original["net_pnl"],
                "filtered_net_pnl": filtered["net_pnl"],
                "original_max_drawdown_pct": original["max_drawdown_pct"],
                "filtered_max_drawdown_pct": filtered["max_drawdown_pct"],
            }
        )
    return rows


def feature_importance_rows(trained: TrainedModel) -> list[dict]:
    """Per-feature CatBoost importance for one trained model."""
    importances = trained.model.get_feature_importance()
    return [
        {
            "finalist": trained.finalist,
            "cost_scenario": trained.cost_scenario,
            "feature": feat,
            "importance": float(imp),
        }
        for feat, imp in zip(trained.feature_columns, importances)
    ]


# ---------------------------------------------------------------------------
# Ranking (spec section 12)
# ---------------------------------------------------------------------------

def rank_candidates(filtered_summary: pd.DataFrame) -> pd.DataFrame:
    """Filter to genuinely-improved candidates and sort by ``ml_filter_score``."""
    if filtered_summary.empty:
        return filtered_summary.assign(ml_filter_score=[])

    df = filtered_summary.copy()
    pf_gain = df["filtered_profit_factor"].apply(_clip_pf) - df[
        "original_profit_factor"
    ].apply(_clip_pf)
    r_gain = df["filtered_average_r"] - df["original_average_r"]
    df["ml_filter_score"] = (
        pf_gain * 30.0
        + r_gain * 30.0
        + df["filtered_net_pnl"] / 100.0
        - df["filtered_max_drawdown_pct"]
        + df["filtered_trades"] / 10.0
    )

    mask = (
        (df["filtered_trades"] >= 20)
        & (df["filtered_profit_factor"] > df["original_profit_factor"])
        & (df["filtered_average_r"] > df["original_average_r"])
        & (df["filtered_max_drawdown_pct"] <= df["original_max_drawdown_pct"])
        & (df["test_status"] != "weak_threshold")
    )
    return (
        df[mask]
        .sort_values("ml_filter_score", ascending=False)
        .reset_index(drop=True)
    )


# ---------------------------------------------------------------------------
# Validation checks (spec section 15)
# ---------------------------------------------------------------------------

def validate_dataset(dataset: pd.DataFrame, feature_columns: list[str]) -> list[str]:
    """Run the explicit no-leakage / integrity checks; return human-readable notes.

    Hard violations raise; soft observations are returned as strings so the
    runner can print them.
    """
    notes: list[str] = []

    # 1. No feature column name looks like an outcome / target / future value.
    ml_features.assert_feature_names_clean(feature_columns)
    notes.append(f"feature-name leakage check passed ({len(feature_columns)} cols)")

    # 2. Feature timestamps are <= signal_time (entry strictly after signal,
    #    exit at/after entry). This is structural proof of no-lookahead.
    bad_entry = int((dataset["entry_time"] <= dataset["signal_time"]).sum())
    if bad_entry:
        raise ValueError(f"{bad_entry} rows have entry_time <= signal_time (lookahead)")
    bad_exit = int((dataset["exit_time"] < dataset["entry_time"]).sum())
    if bad_exit:
        raise ValueError(f"{bad_exit} rows have exit_time < entry_time")
    notes.append("entry_time > signal_time and exit_time >= entry_time for all rows")

    # 3. signal_id is unique per (finalist, config, cost scenario, signal_time).
    dup = int(dataset["signal_id"].duplicated().sum())
    if dup:
        raise ValueError(f"{dup} duplicate signal_id rows (must be unique per config)")
    notes.append("signal_id is unique across the dataset")

    # 4. Time-split ordering: train < validation < test by signal_time.
    if "split" in dataset.columns:
        for fin, grp in dataset.groupby("finalist", sort=False):
            tr = grp[grp["split"] == "train"]["signal_time"]
            va = grp[grp["split"] == "validation"]["signal_time"]
            te = grp[grp["split"] == "test"]["signal_time"]
            if len(tr) and len(va) and tr.max() >= va.min():
                raise ValueError(f"{fin}: train max signal_time >= validation min")
            if len(va) and len(te) and va.max() >= te.min():
                raise ValueError(f"{fin}: validation max signal_time >= test min")
        notes.append("train < validation < test by signal_time (per finalist)")

    return notes
