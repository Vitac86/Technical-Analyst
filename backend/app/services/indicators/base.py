from abc import ABC, abstractmethod

import pandas as pd


class Indicator(ABC):
    """Base class for future indicator implementations."""

    name: str
    category: str

    @abstractmethod
    def calculate(self, candles: pd.DataFrame) -> pd.Series | pd.DataFrame:
        """Calculate indicator values from normalized candle data."""
