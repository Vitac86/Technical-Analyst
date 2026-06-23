"""Pydantic request/response models for the Strategy Lab v1.6 API.

These models describe the rule-based strategy lab surface only. Strategy
parameters are intentionally flat and optional: a request supplies just the
fields it wants to override, and the service merges them onto the preset
defaults (so the UI never has to resend a full parameter set, and can never
inject experimental research-only knobs).
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.strategy_lab.presets import OVERRIDABLE_KEYS


# ---------------------------------------------------------------------------
# Shared config body
# ---------------------------------------------------------------------------
class CustomCosts(BaseModel):
    """Cost knobs for the 'Custom' cost scenario (all in MT5 points / $ per lot)."""

    fixed_spread_points: float = 30.0
    slippage_points: float = 0.0
    commission_per_lot_round_turn: float = 0.0
    swap_long_per_lot_per_day: float = 0.0
    swap_short_per_lot_per_day: float = 0.0


class StrategyConfigBody(BaseModel):
    """One strategy configuration: a preset id plus optional overrides.

    Only fields that are explicitly set are treated as overrides
    (:meth:`overrides`); everything else falls back to the preset defaults.
    """

    preset_id: str
    symbol: str = "XAUUSD"
    timeframe: Optional[str] = None

    # --- tunable strategy / exit / risk parameters (preset-relevant subset used) ---
    atr_period: Optional[int] = None
    multiplier: Optional[float] = None
    lookback: Optional[int] = None
    initial_stop_loss_atr: Optional[float] = None
    trailing_stop_atr: Optional[float] = None
    stop_loss_atr: Optional[float] = None
    take_profit_atr: Optional[float] = None
    risk_percent: Optional[float] = None
    leverage: Optional[float] = None
    initial_equity: Optional[float] = None

    # --- costs ---
    cost_scenario: str = "Base"
    custom_costs: Optional[CustomCosts] = None

    # --- optional reporting window ---
    start: Optional[date] = None
    end: Optional[date] = None

    def overrides(self) -> dict:
        """Explicitly-provided tunable parameters (preset defaults fill the rest)."""
        provided = self.model_dump(exclude_unset=True)
        return {k: provided[k] for k in OVERRIDABLE_KEYS if k in provided}

    def custom_costs_dict(self) -> Optional[dict]:
        return self.custom_costs.model_dump() if self.custom_costs else None

    def start_str(self) -> Optional[str]:
        return self.start.isoformat() if self.start else None

    def end_str(self) -> Optional[str]:
        return self.end.isoformat() if self.end else None


# ---------------------------------------------------------------------------
# Presets (A)
# ---------------------------------------------------------------------------
class PresetOut(BaseModel):
    preset_id: str
    display_name: str
    description: str
    strategy_name: str
    family: str
    timeframe: str
    direction_mode: str
    exit_mode: str
    sizing_mode: str
    default_parameters: dict[str, Any]
    allowed_ranges: dict[str, Any]
    research_status: str
    recommended_use: str
    warning_notes: list[str]
    is_default: bool


class PresetsResponse(BaseModel):
    presets: list[PresetOut]
    default_preset_id: str
    cost_scenarios: list[dict[str, Any]]
    ml_filter_enabled: bool = False
    ml_note: str
    disclaimer: str


# ---------------------------------------------------------------------------
# Backtest (B)
# ---------------------------------------------------------------------------
class BacktestRequest(StrategyConfigBody):
    trades_limit: int = Field(default=500, ge=1, le=5000)
    equity_points: int = Field(default=1500, ge=50, le=6000)


class SummaryMetrics(BaseModel):
    initial_equity: Optional[float]
    final_equity: Optional[float]
    total_return_pct: Optional[float]
    net_profit: Optional[float]
    profit_factor: Optional[float]
    max_drawdown_pct: Optional[float]
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: Optional[float]
    average_r: Optional[float]
    median_r: Optional[float]
    average_lots: Optional[float]
    max_effective_leverage: Optional[float]
    average_effective_leverage: Optional[float]
    stop_out_count: int
    insufficient_margin_count: int


class EquityPoint(BaseModel):
    t: int
    equity: Optional[float]
    balance: Optional[float]


class DrawdownPoint(BaseModel):
    t: int
    drawdown_pct: Optional[float]


class TradeRow(BaseModel):
    entry_time: Optional[str]
    exit_time: Optional[str]
    direction: str
    lots: Optional[float]
    entry_price: Optional[float]
    exit_price: Optional[float]
    exit_reason: str
    bars_held: int
    net_pnl: Optional[float]
    r_multiple: Optional[float]
    balance_after_trade: Optional[float]


class PeriodRow(BaseModel):
    period: str
    return_pct: Optional[float]
    max_drawdown_pct: Optional[float]
    trades: int
    profit_factor: Optional[float]
    net_profit: Optional[float]


class DataRange(BaseModel):
    start: Optional[str]
    end: Optional[str]
    bars: int


class ParametersOut(BaseModel):
    strategy: dict[str, Any]
    exit: dict[str, Any]
    risk: dict[str, Any]


class BacktestResponse(BaseModel):
    preset_id: str
    display_name: str
    symbol: str
    timeframe: str
    strategy_name: str
    direction_mode: str
    exit_mode: str
    sizing_mode: str
    cost_scenario: str
    cost_assumptions: dict[str, Any]
    parameters: ParametersOut
    summary: SummaryMetrics
    equity_curve: list[EquityPoint]
    drawdown_series: list[DrawdownPoint]
    trades: list[TradeRow]
    trades_total: int
    trades_truncated: bool
    yearly_summary: list[PeriodRow]
    walk_forward_summary: list[PeriodRow]
    data_range: DataRange
    warnings: list[str]
    research_disclaimer: str
    ml_note: str


# ---------------------------------------------------------------------------
# Compare (C)
# ---------------------------------------------------------------------------
class CompareConfigInput(StrategyConfigBody):
    label: Optional[str] = None


class CompareRequest(BaseModel):
    configs: list[CompareConfigInput]


class CompareRow(BaseModel):
    label: str
    preset_id: str
    timeframe: str
    cost_scenario: str
    metrics: dict[str, Any]


class CompareResponse(BaseModel):
    fields: list[str]
    rows: list[CompareRow]


# ---------------------------------------------------------------------------
# Export (D)
# ---------------------------------------------------------------------------
class ExportConfigRequest(StrategyConfigBody):
    pass
