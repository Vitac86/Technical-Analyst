from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.schemas.instrument import InstrumentRead


class WorkspaceLoadRequest(BaseModel):
    ticker: str
    engine: str = "stock"
    market: str = "shares"
    board: str = "TQBR"
    timeframe: str = "1d"
    start: date
    end: date
    calculate_indicators: bool = True


class LastPriceSummary(BaseModel):
    model_config = ConfigDict(from_attributes=False)

    last_close: float | None = None
    previous_close: float | None = None
    change: float | None = None
    change_percent: float | None = None
    last_timestamp: datetime | None = None


class WorkspaceLoadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=False)

    instrument: InstrumentRead
    candle_sync: dict[str, Any]
    indicator_sync: dict[str, Any] | None = None
    last_price: LastPriceSummary
