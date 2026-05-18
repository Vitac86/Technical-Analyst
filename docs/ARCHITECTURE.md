# Architecture

## Goal

Technical Analyst is a local-first research tool for collecting market data,
calculating technical indicators, generating analysis signals, and viewing the
results in a browser UI.

## Backend

The backend lives in `backend/app` and uses FastAPI. The public API is grouped
under `/api/v1` with endpoints for health, instruments, candles, indicators,
and analysis.

Current modules:

- `core`: application settings and logging.
- `db`: SQLAlchemy base, SQLite engine, session dependency, and ORM models.
- `repositories`: persistence helpers for instruments, candles, and indicators.
- `services`: market data providers, indicator registry, and analysis engines.
- `tasks`: future local task entrypoints such as market data sync.

## Database

SQLite is the default database. SQLAlchemy 2.x models define the first tables:

- `instruments`
- `candles`
- `indicator_values`
- `analysis_signals`

Alembic is configured but no generated migration is included yet.

## Market Data Providers

Providers implement the `MarketDataProvider` interface.

`MoexProvider` is the first intended real provider. It currently contains TODO
methods for fetching instruments and candles from MOEX ISS.

`TradingViewProvider` is only a placeholder boundary for later work. This
project does not implement scraping and does not require TradingView
credentials.

## Indicators

Indicators are grouped by category:

- Trend: SMA, EMA, MACD, ADX
- Momentum: RSI, Stochastic
- Volatility: Bollinger Bands, ATR
- Volume: OBV

The registry maps indicator names to placeholder functions. Formula
implementation and persistence are future work.

## Analysis Engine

`SignalEngine` will combine candles and indicator values into reproducible
research signals. `TargetEngine` will estimate short-term target ideas. Both
are placeholders in this initial structure.

## Frontend

The frontend lives in `frontend/src` and uses React, TypeScript, and Vite.

Current modules:

- `api`: REST client wrappers.
- `components/layout`: application shell.
- `components/charts`: placeholder price and indicator panels.
- `components/dashboard`: instrument and signal summaries.
- `pages`: dashboard and instrument detail screens.
- `types`: API response types.
- `styles`: global application styling.
