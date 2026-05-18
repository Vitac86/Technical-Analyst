from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any


class MarketDataProvider(ABC):
    """Interface for external market data sources."""

    name: str

    @abstractmethod
    async def fetch_instruments(self) -> list[dict[str, Any]]:
        """Fetch available instruments from the provider."""

    @abstractmethod
    async def fetch_candles(
        self,
        ticker: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[dict[str, Any]]:
        """Fetch OHLCV candles for a ticker and time range."""
