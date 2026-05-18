from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class AnalysisSignalBase(BaseModel):
    instrument_id: int
    timeframe: str
    signal_type: str
    direction: str
    strength: str | None = None
    generated_at: datetime
    expires_at: datetime | None = None
    payload: dict[str, Any] | None = None


class AnalysisSignalCreate(AnalysisSignalBase):
    pass


class AnalysisSignalRead(AnalysisSignalBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
