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

- `client_id` must be `trade-api-write` (not `mobile_app`, not `tradeapi`)
- Body must be `application/x-www-form-urlencoded` (URLSearchParams), never JSON
- Successful response fields: `access_token`, `expires_in`, `refresh_expires_in`, `refresh_token`
- If the response includes a rotated `refresh_token`, it is stored in session memory
- A 400 with `error=invalid_grant` means the refresh token is expired; the app shows:
  _"BCS refresh token is invalid or expired. Paste a new token."_

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

Supported timeframe mapping:

| App timeframe | BCS timeFrame |
|---------------|---------------|
| 5m            | M5            |
| 15m           | M15           |
| 1h            | H1            |
| 4h            | H4            |
| 1d            | D             |

**Do NOT use:** `instrumentId`, `interval`, `from`, `to` — these are wrong param names that cause HTTP 400.

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

- Candles are read from `response.bars` (not `candles`, not `data`)
- BCS returns `bars` newest-first. The app sorts ascending before rendering.
- Each bar has `time` (UTC ISO 8601), `open`, `high`, `low`, `close`, `volume`
- The app converts `time` from UTC to Moscow time (+3h) to match MOEX display convention

---

## Safe range policy (initial load)

BCS rejects requests with oversized date ranges. The app enforces safe initial windows:

| Timeframe | Safe window |
|-----------|-------------|
| 5m        | 3 days      |
| 15m       | 10 days     |
| 1h        | 45 days     |
| 4h        | 180 days    |
| 1d        | ~4 years    |

If the user selects a large preset (e.g. 1W on 5m), the initial BCS request is silently trimmed to the safe window. Older bars load lazily as the user pans left.

---

## Lazy older-candle loading

When using BCS mode, panning left near the oldest loaded candle automatically triggers a background load of the next older chunk (same safe window size). The chart:

1. Detects when the visible logical range's left edge is within 5 bars of the oldest bar.
2. Fires a throttled callback (at most once per 800 ms) to `MobileChartPage`.
3. `loadOlderCandles()` calls `loadBcsOlderChunk()` with the oldest candle's timestamp as the upper bound.
4. Older candles are prepended to the in-memory candle array, sorted, and deduplicated.
5. The chart restores the previously visible area (shifted right by the prepended bar count), so the viewport does not jump.
6. A small status pill shows: "Loading older candles…", "No older candles", or an error message.

Lazy loading only runs in BCS mode with a live BCS connection (not BCS→MOEX fallback).

---

## Candle persistence policy

**No candles are ever persisted.** All loaded candles live exclusively in React state / chart instance memory and are discarded on app reload. There is no SQLite, no IndexedDB, no localStorage candle storage, and no background sync.

---

## Instrument fields

- `ticker` comes from the selected instrument's `secid` / ticker (e.g. `SBER`)
- `classCode` comes from the selected instrument's `boardid` / board (e.g. `TQBR`)
- For MOEX shares, `TQBR` is the correct `classCode`; do not use `SPBRU` for Moscow-listed shares

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
- If BCS candle load fails for any reason (auth error, network error, rate limit, 400), the chart automatically loads MOEX data.
- A compact yellow warning is shown: **BCS unavailable, using MOEX**.
- The footer shows: **Data: BCS→MOEX**.
- Lazy older-candle loading is disabled while in fallback mode.

When fallback is disabled:
- BCS failures show a readable error message.
- Existing chart data (if any) is preserved — no black/blank screen.

---

## 400 error handling

A HTTP 400 from BCS means the request parameters are invalid.

- Auth 400 with `error=invalid_grant` → _"BCS refresh token is invalid or expired. Paste a new token."_
- Candles 400 → _"BCS rejected the candle request. Try a smaller range or another timeframe."_

The app parses `type` and `traceId` fields from the 400 response body for internal diagnostics, but never logs or displays them to the user in raw form.

## Order Book (v2.0.0)
GET https://be.broker.ru/trade-api-market-data-connector/api/v1/order-book
Params: ticker, classCode, depth. Auth: Bearer. BCS-only. 2s poll. No persistence.
