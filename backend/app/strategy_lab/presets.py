"""Strategy Lab v1.6: production rule-based strategy presets.

This module defines the **confirmed, rule-based** XAUUSD finalists that the
Strategy Lab UI exposes for backtesting and inspection. It is deliberately small
and declarative: each preset describes a single shortlisted configuration (its
default parameters, the ranges a user may tweak, and human-facing guidance),
plus the pure helpers needed to merge user overrides, validate them, generate
signals and build a :class:`~app.strategy_lab.risk_backtester.RiskConfig`.

Design rules (see Strategy Lab v1.6 task):

    * Only the two **production** finalists are exposed -- D (primary) and C
      (secondary). The other research finalists (A/B fixed-lot variants) and the
      v1.2/v1.3 random-sampling space stay in the research runners.
    * The ML signal filter (v1.5/v1.5.1) is **not** wired in here. It did not
      improve the rule-based finalists on the held-out 2025-2026 test period, so
      it remains research-only and disabled by default. ``ML_RESEARCH_NOTE``
      below is the single source of truth for that note.
    * No backtest logic lives here -- signal generation reuses
      :mod:`app.strategy_lab.strategies`, and the account simulation reuses
      :mod:`app.strategy_lab.risk_backtester`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

try:  # package import
    from . import strategies
    from .risk_backtester import RiskConfig
except ImportError:  # script import
    import strategies  # type: ignore[no-redef]
    from risk_backtester import RiskConfig  # type: ignore[no-redef]


# Shown verbatim in the UI; ML stays research-only and disabled by default.
ML_RESEARCH_NOTE: str = (
    "The ML signal filter was tested in v1.5/v1.5.1 and is disabled by default "
    "because it did not improve the rule-based finalists on the held-out "
    "2025-2026 test period. It remains research-only and is not part of this UI."
)

RESEARCH_DISCLAIMER: str = (
    "Research and backtesting only. Not investment advice and not live trading. "
    "Past simulated performance does not guarantee future results."
)

# Donchian (finalist C) has no ATR in its signal, but its fixed-ATR stop still
# needs an ATR period. 14 keeps it comparable to the v1.2-v1.4 research that
# surfaced and confirmed this finalist.
DONCHIAN_STOP_ATR_PERIOD: int = 14

# Keys whose user-supplied values may override a preset default. Anything else
# in a request body is ignored (the UI never sends experimental research knobs).
OVERRIDABLE_KEYS: tuple[str, ...] = (
    "atr_period",
    "multiplier",
    "lookback",
    "initial_stop_loss_atr",
    "trailing_stop_atr",
    "stop_loss_atr",
    "take_profit_atr",
    "risk_percent",
    "leverage",
    "initial_equity",
)


# ---------------------------------------------------------------------------
# Preset definition
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class StrategyPreset:
    """One confirmed, rule-based strategy the UI can run.

    ``strategy_keys``/``exit_keys``/``sizing_keys`` partition the tunable
    parameters into the three :class:`RiskConfig` concerns. ``atr_period_source``
    names the parameter that also drives the stop-loss ATR period (SuperTrend's
    ``atr_period``); presets without one fall back to ``default_stop_atr_period``.
    """

    preset_id: str
    display_name: str
    description: str
    family: str  # 'supertrend' | 'donchian'
    strategy_name: str  # human label, e.g. "SuperTrend"
    timeframe: str
    direction_mode: str
    exit_mode: str
    sizing_mode: str
    strategy_keys: tuple[str, ...]
    exit_keys: tuple[str, ...]
    sizing_keys: tuple[str, ...]
    defaults: dict
    allowed_ranges: dict
    research_status: str
    recommended_use: str
    warning_notes: tuple[str, ...]
    atr_period_source: Optional[str] = None
    default_stop_atr_period: int = DONCHIAN_STOP_ATR_PERIOD
    extra_notes: tuple[str, ...] = field(default_factory=tuple)


PRESETS: dict[str, StrategyPreset] = {
    # D -- primary, low-drawdown risk-% SuperTrend on H4 with ATR trailing exit.
    "D_supertrend_h4_trailing_risk": StrategyPreset(
        preset_id="D_supertrend_h4_trailing_risk",
        display_name="D · SuperTrend H4 — ATR trailing (risk %)",
        description=(
            "Primary production finalist. Long-only SuperTrend trend-following on "
            "the H4 timeframe with a wide ATR trailing stop and risk-percent "
            "position sizing. Selected for its low drawdown and consistent "
            "walk-forward behaviour on XAUUSD."
        ),
        family="supertrend",
        strategy_name="SuperTrend",
        timeframe="H4",
        direction_mode="long_only",
        exit_mode="atr_trailing",
        sizing_mode="risk_percent",
        strategy_keys=("atr_period", "multiplier"),
        exit_keys=("initial_stop_loss_atr", "trailing_stop_atr", "take_profit_atr"),
        sizing_keys=("risk_percent",),
        atr_period_source="atr_period",
        defaults={
            "atr_period": 10,
            "multiplier": 2.0,
            "initial_stop_loss_atr": 2.5,
            "trailing_stop_atr": 6.0,
            "take_profit_atr": None,
            "risk_percent": 1.0,
            "leverage": 50.0,
            "initial_equity": 10000.0,
        },
        allowed_ranges={
            "atr_period": {"type": "int", "min": 5, "max": 30},
            "multiplier": {"type": "float", "min": 1.0, "max": 5.0},
            "initial_stop_loss_atr": {"type": "float", "min": 1.0, "max": 6.0},
            "trailing_stop_atr": {"type": "float", "min": 2.0, "max": 12.0},
            "take_profit_atr": {"type": "float", "min": 4.0, "max": 60.0, "nullable": True},
            "risk_percent": {"type": "float", "min": 0.1, "max": 5.0},
            "leverage": {"type": "float", "min": 1.0, "max": 500.0},
            "initial_equity": {"type": "float", "min": 100.0, "max": 10_000_000.0},
        },
        research_status="confirmed_finalist",
        recommended_use=(
            "Primary candidate for a slow, trend-following long-only XAUUSD robot. "
            "Trailing exit lets winners run; risk-% sizing keeps drawdown shallow."
        ),
        warning_notes=(
            "Long-only: it holds no position in sustained down-trends.",
            "Trailing-stop strategies give back open profit on reversals — "
            "expect a lower win rate offset by larger average winners.",
            "Leverage only affects affordability/stop-out, not PnL at a fixed "
            "lot size; 50x is the modelled account default.",
        ),
    ),
    # C -- secondary, high-return risk-% Donchian breakout on H1, fixed-ATR exit.
    "C_donchian_h1_fixed_atr_risk": StrategyPreset(
        preset_id="C_donchian_h1_fixed_atr_risk",
        display_name="C · Donchian H1 breakout — fixed ATR (risk %)",
        description=(
            "Secondary production finalist. Long-only Donchian channel breakout on "
            "the H1 timeframe with a fixed ATR stop-loss and take-profit, and "
            "risk-percent position sizing. A faster, higher-turnover complement to "
            "the SuperTrend trend-follower."
        ),
        family="donchian",
        strategy_name="Donchian breakout",
        timeframe="H1",
        direction_mode="long_only",
        exit_mode="fixed_atr",
        sizing_mode="risk_percent",
        strategy_keys=("lookback",),
        exit_keys=("stop_loss_atr", "take_profit_atr"),
        sizing_keys=("risk_percent",),
        atr_period_source=None,  # Donchian signal has no ATR -> fixed stop period.
        default_stop_atr_period=DONCHIAN_STOP_ATR_PERIOD,
        defaults={
            "lookback": 40,
            "stop_loss_atr": 2.5,
            "take_profit_atr": 16.0,
            "risk_percent": 1.0,
            "leverage": 50.0,
            "initial_equity": 10000.0,
        },
        allowed_ranges={
            "lookback": {"type": "int", "min": 10, "max": 120},
            "stop_loss_atr": {"type": "float", "min": 1.0, "max": 6.0},
            "take_profit_atr": {"type": "float", "min": 4.0, "max": 60.0, "nullable": True},
            "risk_percent": {"type": "float", "min": 0.1, "max": 5.0},
            "leverage": {"type": "float", "min": 1.0, "max": 500.0},
            "initial_equity": {"type": "float", "min": 100.0, "max": 10_000_000.0},
        },
        research_status="confirmed_finalist",
        recommended_use=(
            "Secondary candidate for a faster long-only XAUUSD breakout robot. "
            "Fixed ATR stop/target gives a defined per-trade risk:reward."
        ),
        warning_notes=(
            "Long-only breakout: prone to whipsaw in range-bound regimes.",
            "Higher trade frequency on H1 makes it more sensitive to spread, "
            "slippage and commission — check the Conservative/Stress scenarios.",
            "The stop ATR uses a fixed period of 14 (the research default); the "
            "Donchian lookback only drives the breakout level.",
        ),
        extra_notes=(
            f"Stop-loss ATR period fixed at {DONCHIAN_STOP_ATR_PERIOD}.",
        ),
    ),
}

DEFAULT_PRESET_ID: str = "D_supertrend_h4_trailing_risk"


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------
def get_preset(preset_id: str) -> StrategyPreset:
    """Return the preset for ``preset_id`` or raise ``KeyError`` with a clear list."""
    try:
        return PRESETS[preset_id]
    except KeyError as exc:
        valid = ", ".join(PRESETS)
        raise KeyError(f"Unknown preset_id '{preset_id}'. Valid presets: {valid}") from exc


# ---------------------------------------------------------------------------
# Parameter merge + validation
# ---------------------------------------------------------------------------
def merge_parameters(preset: StrategyPreset, overrides: dict) -> dict:
    """Start from the preset defaults and apply only recognised user overrides.

    ``overrides`` is the set of *explicitly provided* request fields. Keys not in
    :data:`OVERRIDABLE_KEYS` (or not relevant to this preset) are ignored, so the
    UI can never inject experimental research knobs.
    """
    merged = dict(preset.defaults)
    for key in OVERRIDABLE_KEYS:
        if key in overrides and key in preset.defaults:
            merged[key] = overrides[key]
    return merged


def validate_parameters(preset: StrategyPreset, merged: dict) -> list[str]:
    """Return a list of human-readable validation errors (empty == valid)."""
    errors: list[str] = []
    for key, spec in preset.allowed_ranges.items():
        if key not in merged:
            continue
        value = merged[key]

        if value is None:
            if spec.get("nullable"):
                continue
            errors.append(f"'{key}' must not be null")
            continue

        if not isinstance(value, (int, float)) or isinstance(value, bool):
            errors.append(f"'{key}' must be a number")
            continue

        if spec["type"] == "int" and float(value) != int(value):
            errors.append(f"'{key}' must be an integer")

        if "min" in spec and value < spec["min"]:
            errors.append(f"'{key}' must be >= {spec['min']} (got {value})")
        if "max" in spec and value > spec["max"]:
            errors.append(f"'{key}' must be <= {spec['max']} (got {value})")

    return errors


# ---------------------------------------------------------------------------
# Signal generation (reuses app.strategy_lab.strategies)
# ---------------------------------------------------------------------------
def generate_signals(preset: StrategyPreset, df: pd.DataFrame, merged: dict) -> pd.DataFrame:
    """Build the rule-based signal frame for ``preset`` with merged parameters."""
    if preset.family == "supertrend":
        return strategies.supertrend_strategy(
            df,
            atr_period=int(merged["atr_period"]),
            multiplier=float(merged["multiplier"]),
        )
    if preset.family == "donchian":
        return strategies.donchian_breakout_strategy(
            df, lookback=int(merged["lookback"])
        )
    raise ValueError(f"Unsupported strategy family '{preset.family}'")


def stop_atr_period(preset: StrategyPreset, merged: dict) -> int:
    """ATR period used for the protective stop (signal ATR for SuperTrend)."""
    if preset.atr_period_source:
        return int(merged[preset.atr_period_source])
    return preset.default_stop_atr_period


def build_risk_config(
    preset: StrategyPreset,
    merged: dict,
    cost_kwargs: dict,
    account_defaults: dict,
) -> RiskConfig:
    """Assemble a :class:`RiskConfig` from a preset, merged params and costs.

    ``cost_kwargs`` and ``account_defaults`` are supplied by the service layer so
    this module stays free of cost-scenario tables.
    """
    kwargs = dict(
        account_defaults,
        initial_equity=float(merged["initial_equity"]),
        leverage=float(merged["leverage"]),
        atr_period=stop_atr_period(preset, merged),
        direction_mode=preset.direction_mode,
        exit_mode=preset.exit_mode,
        sizing_mode=preset.sizing_mode,
    )
    kwargs.update(cost_kwargs)
    for key in (*preset.exit_keys, *preset.sizing_keys):
        kwargs[key] = merged[key]
    return RiskConfig(**kwargs)


# ---------------------------------------------------------------------------
# Export config (reused by the export-config endpoint)
# ---------------------------------------------------------------------------
def split_parameters(preset: StrategyPreset, merged: dict) -> tuple[dict, dict, dict]:
    """Split merged params into (strategy, exit, risk) groups for export/echo."""
    strategy = {k: merged[k] for k in preset.strategy_keys}
    exit_params = {k: merged[k] for k in preset.exit_keys}
    risk = {k: merged[k] for k in preset.sizing_keys}
    risk["leverage"] = merged["leverage"]
    risk["initial_equity"] = merged["initial_equity"]
    return strategy, exit_params, risk
