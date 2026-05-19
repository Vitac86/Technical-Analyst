from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

ScanStatus = Literal["ok", "no_instrument", "no_candles", "no_indicators", "error"]


class ScannerInstrumentRequest(BaseModel):
    ticker: str
    engine: str = "stock"
    market: str = "shares"
    board: str = "TQBR"


class ScannerRequest(BaseModel):
    instruments: list[ScannerInstrumentRequest]
    timeframe: str = "1d"
    lookback: int = 100


class ScannerRow(BaseModel):
    ticker: str
    name: str | None = None
    engine: str | None = None
    market: str | None = None
    board: str | None = None
    instrument_id: int | None = None
    timeframe: str
    status: ScanStatus
    last_close: float | None = None
    change_percent: float | None = None
    aggregate_signal: str | None = None
    total_score: int | None = None
    confidence: str | None = None
    bullish_count: int | None = None
    bearish_count: int | None = None
    caution_count: int | None = None
    rsi: float | None = None
    macd_histogram: float | None = None
    atr_percent: float | None = None
    nearest_support: float | None = None
    nearest_resistance: float | None = None
    distance_to_support_percent: float | None = None
    distance_to_resistance_percent: float | None = None
    summary: str | None = None
    error: str | None = None
    last_timestamp: datetime | None = None


class ScannerResponse(BaseModel):
    timeframe: str
    rows: list[ScannerRow]
    generated_at: datetime
