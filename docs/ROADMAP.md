# Roadmap

## Phase 1: Foundation

- Create project structure for backend, frontend, docs, and scripts.
- Add FastAPI health endpoint.
- Configure SQLite, SQLAlchemy, and Alembic.
- Add initial ORM models and Pydantic schemas.
- Add provider, indicator, and analysis service boundaries.
- Add Vite React dashboard skeleton.

## Phase 2: Database Migrations

- Generate the initial Alembic migration.
- Add database creation instructions.
- Add repository tests for model persistence.

## Phase 3: MOEX Data Sync

- Implement MOEX ISS instrument fetching.
- Implement MOEX candle fetching by instrument and timeframe.
- Normalize provider responses.
- Add upsert logic for instruments and candles.
- Add a local sync command.

## Phase 4: Indicator Calculations

- Implement SMA, EMA, RSI, MACD, Bollinger Bands, ATR, ADX, OBV, and
  Stochastic calculations.
- Add indicator batch calculation services.
- Persist indicator outputs to SQLite.
- Add tests against small known datasets.

## Phase 5: Analysis Signals

- Define signal rules and confidence metadata.
- Generate trend, momentum, volatility, and volume signals.
- Implement short-term target estimation.
- Store generated signals with reproducible payloads.

## Phase 6: Frontend Data Integration

- Replace placeholder dashboard data with API calls.
- Add loading, empty, and error states.
- Add instrument search and filtering.
- Add charting for candles and indicators.
- Add signal and target views.

## Phase 7: TradingView Provider Boundary

- Revisit the provider interface after MOEX is stable.
- Add optional TradingView support only through a separate provider layer.
- Avoid scraping or credential assumptions unless explicitly designed later.
