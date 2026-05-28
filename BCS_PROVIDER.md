# BCS Market Data Provider

Experimental support for loading historical candles from BCS Broker API.

---

## How to get a BCS read-only token

1. Log in to your BCS account at [bcs.ru](https://bcs.ru).
2. Navigate to **API / Personal Access Tokens** (exact path may vary by interface version).
3. Generate a **read-only** token for market data access.
4. Copy the **refresh token** (not the access token — it expires sooner).

> **Security note:** Always use a read-only token. Never paste a trading token into the app.

---

## Where to paste the token

1. Open the app and tap the **☰** (hamburger) button to open the asset drawer.
2. At the bottom of the drawer, tap **⚙ Data source**.
3. In **Data Provider** settings, select **BCS**.
4. Paste your refresh token into the masked input field.
5. Tap **Save token**.
6. Optionally tap **Test** to verify connectivity.

---

## Token lifecycle

| Token type    | Typical lifetime | Storage |
|---------------|-----------------|---------|
| Refresh token | ~90 days        | Session memory only (lost on app restart) |
| Access token  | ~24 hours       | Session memory only, auto-refreshed |

Because tokens are stored in session memory only (not localStorage, not IndexedDB), **you must re-enter the refresh token after every app restart or page reload**.

---

## Security notes

- Use a **read-only** BCS token only.
- Do **not** use a trading token — the app makes no trading calls, but a leaked trading token is a security risk.
- Tokens are **never logged** anywhere in the app.
- Tokens are **never persisted** in localStorage or IndexedDB.
- The full token is never shown in the UI after saving.

---

## Verified API endpoints

**Base URL:** `https://be.broker.ru`

### Authentication

```
POST /trade-api-keycloak/realms/tradeapi/protocol/openid-connect/token
Content-Type: application/x-www-form-urlencoded
Accept: application/json

client_id=trade-api-write
grant_type=refresh_token
refresh_token=<token>
```

Successful response fields: `access_token`, `expires_in`, `refresh_expires_in`, `refresh_token`

### Historical candles

```
GET /trade-api-market-data-connector/api/v1/candles-chart

Query params:
  ticker      — instrument ticker (e.g. SBER)
  classCode   — board code (e.g. TQBR)
  startDate   — ISO 8601 UTC (e.g. 2026-05-25T00:00:00.000Z)
  endDate     — ISO 8601 UTC
  timeFrame   — M1 | M5 | M15 | M30 | H1 | H4 | D | W | MN
```

Response shape:
```json
{
  "ticker": "SBER",
  "classCode": "TQBR",
  "startDate": "...",
  "endDate": "...",
  "timeFrame": "M5",
  "bars": [
    { "time": "2026-05-28T12:40:00.000Z", "open": 322.58, "close": 322.62, "high": 322.62, "low": 322.5, "volume": 4096128.0 }
  ]
}
```

> **Note:** BCS returns `bars` newest-first. The app sorts ascending before rendering.

### Instrument lookup

```
POST /trade-api-information-service/api/v1/instruments/by-tickers
Content-Type: application/json

{ "tickers": ["SBER"] }
```

---

## Limitations

- BCS support is **experimental**.
- No orders, no trading, no portfolio operations — the app is read-only for market data only.
- No candle caching or background sync.
- Live polling is **disabled** in BCS mode (candles are loaded on demand / manual refresh).
- Watchlist quotes continue to use **MOEX** (BCS quote endpoint not mapped).
- If BCS fails and **Fallback to MOEX** is enabled, the chart loads MOEX data and shows a compact warning.

---

## Fallback behavior

When **Fallback to MOEX** is enabled (default):
- If BCS candle load fails for any reason (auth error, network error, rate limit), the chart automatically loads MOEX data.
- A compact yellow warning is shown: **BCS unavailable, using MOEX**.
- The footer shows: **Data: BCS→MOEX**.

When fallback is disabled:
- BCS failures show a readable error message.
- Existing chart data (if any) is preserved — no black/blank screen.
