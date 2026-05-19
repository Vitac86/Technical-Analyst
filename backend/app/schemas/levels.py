from datetime import datetime
from typing import Literal

from pydantic import BaseModel

LevelKind = Literal["support", "resistance", "target_up", "target_down", "stop_zone", "info"]


class TechnicalLevel(BaseModel):
    kind: LevelKind
    label: str
    price: float | None
    distance_percent: float | None
    reason: str


class TechnicalLevelsResponse(BaseModel):
    instrument_id: int
    timeframe: str
    last_close: float | None
    atr: float | None
    atr_percent: float | None
    lookback: int
    levels: list[TechnicalLevel]
    summary: str
    generated_at: datetime
    message: str | None = None
