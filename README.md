# Technical Analyst

Local browser-based technical analysis workspace for personal trading research.

The project is intentionally small at this stage: FastAPI, SQLite, Alembic, and
React/Vite provide the base for loading MOEX market data and building analysis
features on top of stored candles.

## Stack

- Backend: Python, FastAPI, SQLAlchemy 2.x, Alembic
- Database: SQLite
- Data processing: pandas, numpy
- Frontend: React, TypeScript, Vite
- API: REST under `/api/v1`
- First market data source: MOEX ISS
- Future optional provider boundary: TradingView

## Backend Setup

```powershell
cd "C:\Users\Виталий\Desktop\PythonProjects\Tecnical Analyst\backend"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
copy .env.example .env
python -m alembic upgrade head
python -m uvicorn app.main:app --reload
```

Health check:

```powershell
Invoke-RestMethod http://localhost:8000/api/v1/health
```

Run backend tests:

```powershell
cd "C:\Users\Виталий\Desktop\PythonProjects\Tecnical Analyst\backend"
.\.venv\Scripts\python.exe -m pytest
```

## Database

The default SQLite URL is:

```text
sqlite:///./technical_analyst.db
```

Run migrations from `backend/`:

```powershell
cd "C:\Users\Виталий\Desktop\PythonProjects\Tecnical Analyst\backend"
.\.venv\Scripts\python.exe -m alembic upgrade head
```

## MOEX Sync

Sync MOEX TQBR share instruments:

```powershell
cd "C:\Users\Виталий\Desktop\PythonProjects\Tecnical Analyst\backend"
.\.venv\Scripts\python.exe -m app.tasks.sync_market_data instruments
```

Sync SBER daily candles:

```powershell
cd "C:\Users\Виталий\Desktop\PythonProjects\Tecnical Analyst\backend"
.\.venv\Scripts\python.exe -m app.tasks.sync_market_data candles --ticker SBER --timeframe 1d --start 2024-01-01 --end 2024-03-01
```

API sync examples while the backend is running:

```powershell
Invoke-RestMethod -Method Post http://localhost:8000/api/v1/sync/moex/instruments
Invoke-RestMethod -Method Post "http://localhost:8000/api/v1/sync/moex/candles?ticker=SBER&timeframe=1d&start=2024-01-01&end=2024-03-01"
```

Inspect stored data:

```powershell
Invoke-RestMethod http://localhost:8000/api/v1/instruments
Invoke-RestMethod "http://localhost:8000/api/v1/candles?instrument_id=1&timeframe=1d"
```

Supported MOEX candle timeframes: `1m`, `10m`, `1h`, `1d`, `1w`, `1mo`.

## Indicator Calculation

After syncing candles, calculate the default indicator set from `backend/`:

```powershell
cd backend
python -m alembic upgrade head
python -m app.tasks.sync_market_data instruments
python -m app.tasks.sync_market_data candles --ticker SBER --timeframe 1d --start 2024-01-01 --end 2024-06-01
python -m app.tasks.calculate_indicators defaults --instrument-id 1 --timeframe 1d
```

Calculate one indicator with custom parameters:

```powershell
python -m app.tasks.calculate_indicators one --instrument-id 1 --timeframe 1d --indicator rsi --window 14
python -m app.tasks.calculate_indicators one --instrument-id 1 --timeframe 1d --indicator sma --window 50
```

Stored indicator names use stable parameter suffixes:

```text
sma_20
ema_20
rsi_14
macd_12_26_9
bollinger_bands_20_2
atr_14
```

API examples while the backend is running:

```powershell
Invoke-RestMethod -Method Post "http://localhost:8000/api/v1/indicators/calculate-defaults?instrument_id=1&timeframe=1d"
Invoke-RestMethod -Method Post "http://localhost:8000/api/v1/indicators/calculate?instrument_id=1&timeframe=1d&indicator_name=rsi_14"
Invoke-RestMethod -Method Post -ContentType "application/json" -Body '{"instrument_id":1,"timeframe":"1d","indicator_name":"macd","params":{"fast_period":12,"slow_period":26,"signal_period":9}}' http://localhost:8000/api/v1/indicators/calculate
```

Inspect stored indicator values:

```powershell
Invoke-RestMethod "http://localhost:8000/api/v1/indicators?instrument_id=1&indicator_name=rsi_14"
sqlite3 technical_analyst.db "select indicator_name, timeframe, timestamp, values from indicator_values order by timestamp limit 5;"
```

## Frontend Setup

```powershell
cd "C:\Users\Виталий\Desktop\PythonProjects\Tecnical Analyst\frontend"
npm.cmd install
npm.cmd run dev
```

Open the Vite URL shown in the terminal, usually `http://localhost:5173`.

Build check:

```powershell
cd "C:\Users\Виталий\Desktop\PythonProjects\Tecnical Analyst\frontend"
npm.cmd run build
```
