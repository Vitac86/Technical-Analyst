from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

SignalDirection = Literal["buy", "sell", "neutral", "caution", "info"]
SignalStrength = Literal["weak", "medium", "strong", "info"]
AggregateSignal = Literal["strong_buy", "buy", "neutral", "sell", "strong_sell", "caution", "no_data"]
Confidence = Literal["low", "medium", "high"]


class TechnicalSignalItem(BaseModel):
    indicator_name: str
    label: str
    value: dict[str, Any] | float | str | None
    signal: SignalDirection
    score: int
    strength: SignalStrength
    reason: str
    timestamp: datetime | None


class TechnicalSignalAggregate(BaseModel):
    instrument_id: int
    timeframe: str
    total_score: int
    signal: AggregateSignal
    confidence: Confidence
    bullish_count: int
    bearish_count: int
    caution_count: int
    info_count: int
    generated_at: datetime


class TechnicalSignalResponse(BaseModel):
    instrument_id: int
    timeframe: str
    aggregate: TechnicalSignalAggregate
    signals: list[TechnicalSignalItem]
    message: str | None = None
