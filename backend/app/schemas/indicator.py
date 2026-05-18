from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class IndicatorValueBase(BaseModel):
    instrument_id: int
    indicator_name: str
    category: str
    timeframe: str
    timestamp: datetime
    values: dict[str, Any]


class IndicatorValueCreate(IndicatorValueBase):
    pass


class IndicatorValueRead(IndicatorValueBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
