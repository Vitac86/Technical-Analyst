"""Strategy Lab: a lightweight backtesting core for MT5 historical data.

A self-contained, dependency-light (pandas/numpy only) research toolkit for
evaluating rule-based trading strategies on exported MetaTrader 5 CSV data.

The package is intentionally decoupled from the FastAPI application: it does
not import the app, touch the database, or change any existing behaviour. It is
meant to be driven from :mod:`app.strategy_lab.run_backtests` or imported
directly for ad-hoc research.

Version 1 scope:
    * fixed/bar spread cost modelling
    * ATR-based stop-loss / take-profit exits
    * single position at a time, long and short
    * no machine learning, no UI
"""

__all__ = [
    "data_loader",
    "indicators",
    "strategies",
    "backtester",
    "metrics",
]
