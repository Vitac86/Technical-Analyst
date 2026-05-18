from collections.abc import Callable

import pandas as pd

from app.services.indicators import momentum, trend, volatility, volume


IndicatorFunction = Callable[..., pd.Series | pd.DataFrame]

INDICATOR_REGISTRY: dict[str, dict[str, IndicatorFunction]] = {
    "trend": {
        "sma": trend.sma,
        "ema": trend.ema,
        "macd": trend.macd,
        "adx": trend.adx,
    },
    "momentum": {
        "rsi": momentum.rsi,
        "stochastic": momentum.stochastic,
    },
    "volatility": {
        "bollinger_bands": volatility.bollinger_bands,
        "atr": volatility.atr,
    },
    "volume": {
        "obv": volume.obv,
    },
}


def get_indicator(name: str) -> IndicatorFunction | None:
    """Return an indicator function by registry key."""
    normalized_name = name.lower()
    for category in INDICATOR_REGISTRY.values():
        if normalized_name in category:
            return category[normalized_name]
    return None
