# Technical Analyst

Local browser-based technical analysis workspace for personal trading research.

FastAPI + SQLite + Alembic + React/Vite + TradingView Lightweight Charts.

## Supported timeframes

| App timeframe | MOEX fetch | Aggregation |
|---------------|------------|-------------|
| `1d`          | 1d candles | none        |
| `1h`          | 1h candles | none        |
| `4h`          | 1h candles | → 4h buckets |
| `15m`         | 1m candles | → 15m buckets |
| `5m`          | 1m candles | → 5m buckets  |

Aggregation runs in the backend before persisting. The frontend always receives
candles and indicators keyed by the **app timeframe** label (`5m`, `15m`, `4h`).
MOEX ISS implementation intervals are never exposed to the frontend.

## Supported market sources

Instruments are identified by a **source tuple**: `engine / market / board / ticker`.

| Segment       | engine     | market  | board  | Example ticker |
|---------------|------------|---------|--------|---------------|
| Shares (main) | `stock`    | `shares`| `TQBR` | `SBER`        |
| FX spot       | `currency` | `selt`  | `CETS` | `USD000UTSTOM`|
| Futures       | `futures`  | `forts` | `RFUD` | `SiH5`        |

A full-market instrument sync is **not required** for normal use.  Search and
single-instrument load work without it.

## One-command Windows launch

### Start

```powershell
.\scripts\start_app.ps1
```

Or double-click **`scripts\start_app.bat`**.

The script:
- stops any stale listeners on ports 8001 and 5173
- starts the backend (FastAPI/uvicorn) on **port 8001** — no `--reload`
- starts the frontend (Vite dev server) on **port 5173**
- waits for both to be healthy
- opens the browser at **`http://127.0.0.1:5173/chart`**
- writes logs to `logs\backend.log` and `logs\frontend.log`

### Stop

```powershell
.\scripts\stop_app.ps1
```

Or double-click **`scripts\stop_app.bat`**.

### Restart

```powershell
.\scripts\restart_app.ps1
```

Or double-click **`scripts\restart_app.bat`**.

### Notes

- Backend default port: **8001**
- Frontend default port: **5173**
- Normal launch does **not** use `uvicorn --reload`
- Logs: `logs\backend.log` (uvicorn stderr) and `logs\frontend.log` (Vite stdout)
- The `logs\` directory is created automatically and is git-ignored

---

## Quick start — using the chart UI (no command line)

1. Start the backend (once):

```powershell
cd "C:\Users\Виталий\Desktop\PythonProjects\Tecnical Analyst\backend"
.\.venv\Scripts\Activate.ps1
python -m alembic upgrade head
python -m uvicorn app.main:app --reload
```

2. Start the frontend (in another terminal):

```powershell
cd "C:\Users\Виталий\Desktop\PythonProjects\Tecnical Analyst\frontend"
npm.cmd install
npm.cmd run dev
```

3. Open `http://localhost:5173/chart`.

4. In the **Search instrument** box, type a ticker (e.g. `SBER`, `GAZP`,
   `USD000UTSTOM`).  Select a result from the dropdown.

5. Choose a **Timeframe** (`5m`, `15m`, `1h`, `4h`, `1d`) and a **date range**.

6. Click **Load / update data**.

The backend syncs candles from MOEX ISS, aggregates them to the selected
timeframe if needed, calculates default indicators, and the chart updates
automatically.

7. To change timeframe without re-syncing, adjust the timeframe, click **Load**
   again.  Previously loaded candles are upserted, not duplicated.

8. Click **Recalculate indicators** to rerun indicator math without re-fetching
   MOEX data (useful after adjusting date range in the local DB).

## Stack

- Backend: Python 3.11, FastAPI, SQLAlchemy 2.x, Alembic, pandas/numpy
- Database: SQLite
- Frontend: React 19, TypeScript, Vite
- Charts: TradingView Lightweight Charts 5
- API: REST under `/api/v1`
- Data source: MOEX ISS

## Backend setup

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

## API endpoints

### Instrument search (no sync required)

```powershell
Invoke-RestMethod "http://localhost:8000/api/v1/instruments/search?query=SBER"
Invoke-RestMethod "http://localhost:8000/api/v1/instruments/search?query=USD"
```

### Load workspace (sync + indicators in one call)

```powershell
Invoke-RestMethod -Method Post -ContentType "application/json" `
  -Body '{"ticker":"SBER","engine":"stock","market":"shares","board":"TQBR","timeframe":"1d","start":"2024-01-01","end":"2024-06-01","calculate_indicators":true}' `
  http://localhost:8000/api/v1/workspace
```

Currency / FX example:

```powershell
Invoke-RestMethod -Method Post -ContentType "application/json" `
  -Body '{"ticker":"USD000UTSTOM","engine":"currency","market":"selt","board":"CETS","timeframe":"1d","start":"2024-01-01","end":"2024-06-01","calculate_indicators":true}' `
  http://localhost:8000/api/v1/workspace
```

Futures example:

```powershell
Invoke-RestMethod -Method Post -ContentType "application/json" `
  -Body '{"ticker":"SiH5","engine":"futures","market":"forts","board":"RFUD","timeframe":"1h","start":"2025-01-01","end":"2025-03-01","calculate_indicators":true}' `
  http://localhost:8000/api/v1/workspace
```

### Sync candles (separate, with optional indicator calculation)

```powershell
# Daily candles — direct MOEX fetch
Invoke-RestMethod -Method Post "http://localhost:8000/api/v1/sync/moex/candles?ticker=SBER&timeframe=1d&start=2024-01-01&end=2024-06-01&calculate_indicators=true"

# 1h candles — direct MOEX fetch
Invoke-RestMethod -Method Post "http://localhost:8000/api/v1/sync/moex/candles?ticker=SBER&timeframe=1h&start=2024-01-01&end=2024-02-01&calculate_indicators=true"

# 15m candles — fetches 1m from MOEX, aggregates to 15m
Invoke-RestMethod -Method Post "http://localhost:8000/api/v1/sync/moex/candles?ticker=SBER&timeframe=15m&start=2024-01-15&end=2024-01-20&calculate_indicators=true"

# Currency instrument with explicit source tuple
Invoke-RestMethod -Method Post "http://localhost:8000/api/v1/sync/moex/candles?ticker=USD000UTSTOM&engine=currency&market=selt&board=CETS&timeframe=1d&start=2024-01-01&end=2024-06-01"
```

### Upsert one instrument

```powershell
Invoke-RestMethod -Method Post -ContentType "application/json" `
  -Body '{"ticker":"SBER","engine":"stock","market":"shares","board":"TQBR"}' `
  http://localhost:8000/api/v1/sync/moex/instrument
```

### Full-market instrument sync (optional, stocks only)

```powershell
Invoke-RestMethod -Method Post http://localhost:8000/api/v1/sync/moex/instruments
```

### List and read

```powershell
Invoke-RestMethod http://localhost:8000/api/v1/instruments
Invoke-RestMethod "http://localhost:8000/api/v1/candles?instrument_id=1&timeframe=1d"
Invoke-RestMethod "http://localhost:8000/api/v1/indicators?instrument_id=1&indicator_name=rsi_14&timeframe=1d"
Invoke-RestMethod "http://localhost:8000/api/v1/instruments/1/summary?timeframe=1d"
```

## Indicator calculation (CLI)

Calculate the default indicator set for instrument id 1, timeframe 1d:

```powershell
cd "C:\Users\Виталий\Desktop\PythonProjects\Tecnical Analyst\backend"
.\.venv\Scripts\python.exe -m app.tasks.calculate_indicators defaults --instrument-id 1 --timeframe 1d
```

Default indicators: `sma_20`, `ema_20`, `rsi_14`, `macd_12_26_9`, `bollinger_bands_20_2`, `atr_14`.

## Frontend setup

```powershell
cd "C:\Users\Виталий\Desktop\PythonProjects\Tecnical Analyst\frontend"
npm.cmd install
npm.cmd run dev
```

Build check:

```powershell
npm.cmd run build
```

## Smoke test checklist

- Start backend → `python -m uvicorn app.main:app --reload`
- Start frontend → `npm.cmd run dev`
- Open `http://localhost:5173/chart`
- Type `SBER` in the search box → select from results
- Choose timeframe `1d`, date range 2024-01-01 to 2024-06-01
- Click **Load / update data** → chart appears with candles and indicators
- Change timeframe to `1h` → click Load → hourly chart appears
- Change timeframe to `15m` → click Load → 15m chart appears (MOEX must return enough 1m data)
- Change timeframe to `4h` → click Load → 4h bars appear
- Type `USD` → select `USD000UTSTOM` → change engine/market/board if auto-detected
- Click Load → currency candles appear

## Technical Research Signals

The backend generates per-indicator **Technical Research Signals** based on the
latest stored candle and indicator values for a given instrument and timeframe.

### Signal categories

| Signal | Description |
|--------|-------------|
| `buy` | Indicator suggests bullish momentum |
| `sell` | Indicator suggests bearish momentum |
| `neutral` | Indicator shows no directional bias |
| `caution` | Condition warrants attention (e.g. overbought) |
| `info` | Informational only — ATR volatility classification |

### Aggregate score meaning

Individual indicator scores are summed:

| Total score | Aggregate signal |
|-------------|-----------------|
| ≥ 4 | `strong_buy` |
| 2–3 | `buy` |
| −1 to 1 | `neutral` |
| −3 to −2 | `sell` |
| ≤ −4 | `strong_sell` |

Caution override: if 2+ indicators show `caution` and total score > 0, the
aggregate becomes `caution`.

Confidence is `high` (≥5 actionable indicators), `medium` (3–4), or `low` (<3).

### Covered indicators

`sma_20`, `ema_20`, `rsi_14`, `macd_12_26_9`, `bollinger_bands_20_2`, `atr_14`

### API example

```
GET http://127.0.0.1:8001/api/v1/analysis/technical-signals?instrument_id=192&timeframe=5m
```

PowerShell:

```powershell
Invoke-RestMethod "http://127.0.0.1:8001/api/v1/analysis/technical-signals?instrument_id=192&timeframe=1d"
```

### Disclaimer

Signals are **Technical Research Signals** for personal research use only.
They do not constitute financial advice or recommendations to buy or sell any
security.

---

## Current Quote Snapshot and Auto-refresh

### Quote snapshot endpoint

Returns a near-current market snapshot for one instrument directly from MOEX ISS
without requiring a candle sync.

```
GET http://127.0.0.1:8001/api/v1/quotes/moex?ticker=SBER&engine=stock&market=shares&board=TQBR
```

PowerShell:

```powershell
Invoke-RestMethod "http://127.0.0.1:8001/api/v1/quotes/moex?ticker=SBER&engine=stock&market=shares&board=TQBR"
Invoke-RestMethod "http://127.0.0.1:8001/api/v1/quotes/moex?ticker=USD000UTSTOM&engine=currency&market=selt&board=CETS"
```

Response fields: `ticker`, `engine`, `market`, `board`, `last_price`, `bid`, `ask`,
`open`, `high`, `low`, `close`, `previous_close`, `change`, `change_percent`,
`volume`, `value`, `trade_time`, `server_time`, `source`.

All price fields are `null` when the market is closed or data is unavailable —
this is a normal 200 response.  A 503 is returned only when MOEX ISS itself is unreachable.

### How quote refresh differs from candle sync

| Behaviour | Quote refresh | Candle sync |
|-----------|--------------|-------------|
| Writes to SQLite | No | Yes |
| Fetches history | No | Yes |
| Network call | Yes (MOEX ISS marketdata) | Yes (MOEX ISS candles) |
| This is real streaming | No — polling | No — polling |

The quote card displays near-current data by polling the MOEX ISS on demand.
It does **not** update the chart, indicators, or signals — those still require a
candle sync via **Load / update data**.

### Auto-refresh (UI)

The chart page has an **Auto-refresh** selector (Off / 15 sec / 30 sec / 60 sec).

- **Quote only** (default): calls the quote endpoint every N seconds.
  Candles, indicators, and signals are not touched.
- **Also refresh candles** checkbox: when checked, a full workspace load runs on
  each tick — candles, indicators, and signals are all updated and written to SQLite.
  Overlapping requests are guarded; if the previous tick is still running the new
  tick is skipped.

Status lines below the controls show **last quote refresh time** and (if enabled)
**last candle sync time**.

---

## Known limitations

- **Real-time quotes**: Not supported. The quote snapshot endpoint polls MOEX ISS on demand but is not a streaming feed. Last price in the candle panel is still computed from the last stored candle.
- **5m/15m depth**: MOEX returns 1m data for approximately the last 30 days. Requesting a
  range older than that will return fewer candles or none.
- **4h alignment**: 4h buckets are aligned to midnight UTC. Russian market hours may
  produce partial bars at session open/close.
- **Futures tickers**: Active contract codes (e.g. `SiH5`, `RIH5`) change each quarter.
  You must know the current contract code.
- **Instrument uniqueness**: The DB unique constraint is on `ticker` only. Two instruments
  on different boards with the same ticker are not supported without a schema migration.
  Use the source-tuple repository methods to look them up correctly.
- **ADX, Stochastic, OBV**: Registered but not yet implemented (raise `NotImplementedError`).
