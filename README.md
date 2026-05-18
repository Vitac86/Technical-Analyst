# Technical Analyst

Local browser-based technical analysis workspace for personal trading research.

The project is intentionally small at this stage: it provides the backend,
frontend, database, provider, indicator, and analysis skeletons that later work
can extend.

## Stack

- Backend: Python, FastAPI, SQLAlchemy 2.x, Alembic
- Database: SQLite
- Data processing: pandas, numpy
- Frontend: React, TypeScript, Vite
- API: REST under `/api/v1`
- First planned market data source: MOEX ISS
- Future optional provider boundary: TradingView

## Backend Setup

```powershell
cd "C:\Users\Виталий\Desktop\PythonProjects\Tecnical Analyst\backend"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
copy .env.example .env
uvicorn app.main:app --reload
```

Health check:

```powershell
Invoke-RestMethod http://localhost:8000/api/v1/health
```

Run backend tests:

```powershell
cd "C:\Users\Виталий\Desktop\PythonProjects\Tecnical Analyst\backend"
pytest
```

## Frontend Setup

```powershell
cd "C:\Users\Виталий\Desktop\PythonProjects\Tecnical Analyst\frontend"
npm install
npm run dev
```

Open the Vite URL shown in the terminal, usually `http://localhost:5173`.
If PowerShell blocks `npm.ps1`, use `npm.cmd install` and `npm.cmd run dev`.

Build check:

```powershell
npm run build
```

## Database

The default SQLite URL is:

```text
sqlite:///./technical_analyst.db
```

Alembic is configured in `backend/alembic.ini`. The initial migration is not
created yet; the next backend step should generate it from the SQLAlchemy
models.

## Next Steps

- Generate the initial Alembic migration.
- Implement MOEX ISS instrument and candle syncing.
- Add repository create/upsert methods for synced data.
- Implement core indicator formulas and persistence.
- Connect frontend dashboard data to live API responses.
- Add charting once candle data exists.
