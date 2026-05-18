from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class CandleBase(BaseModel):
    instrument_id: int
    timeframe: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None = None


class CandleCreate(CandleBase):
    pass


class CandleRead(CandleBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
