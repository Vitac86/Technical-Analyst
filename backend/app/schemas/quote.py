from pydantic import BaseModel


class QuoteSnapshot(BaseModel):
    ticker: str
    engine: str
    market: str
    board: str
    last_price: float | None = None
    bid: float | None = None
    ask: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    previous_close: float | None = None
    change: float | None = None
    change_percent: float | None = None
    volume: float | None = None
    value: float | None = None
    trade_time: str | None = None
    server_time: str | None = None
    source: str
