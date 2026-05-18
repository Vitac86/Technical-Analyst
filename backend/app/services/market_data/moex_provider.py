from datetime import datetime
from typing import Any

from app.services.market_data.base import MarketDataProvider


class MoexProvider(MarketDataProvider):
    """MOEX ISS provider placeholder.

    MOEX is intended to be the first real data source. Future work should map
    ISS securities and candle responses into normalized instrument/candle
    records before persistence.
    """

    name = "moex"

    async def fetch_instruments(self) -> list[dict[str, Any]]:
        """Fetch MOEX instruments.

        TODO: Integrate with MOEX ISS securities endpoints.
        """
        raise NotImplementedError("MOEX instrument fetching is not implemented yet.")

    async def fetch_candles(
        self,
        ticker: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[dict[str, Any]]:
        """Fetch MOEX candles for a ticker.

        TODO: Integrate with MOEX ISS candles endpoint and normalize intervals.
        """
        raise NotImplementedError("MOEX candle fetching is not implemented yet.")
