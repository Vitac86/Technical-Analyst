from datetime import datetime
from typing import Any

from app.services.market_data.base import MarketDataProvider


class TradingViewProvider(MarketDataProvider):
    """TradingView provider placeholder.

    This class exists only as a future provider boundary. It intentionally does
    not scrape data, require credentials, or depend on any TradingView library.
    """

    name = "tradingview"

    async def fetch_instruments(self) -> list[dict[str, Any]]:
        """Fetch TradingView instruments in a future provider implementation."""
        raise NotImplementedError("TradingView support is a future provider.")

    async def fetch_candles(
        self,
        ticker: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[dict[str, Any]]:
        """Fetch TradingView candles in a future provider implementation."""
        raise NotImplementedError("TradingView support is a future provider.")
