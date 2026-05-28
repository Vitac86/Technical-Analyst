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

1. Open the app and tap the **⚙** (gear) button in the chart controls row.
2. In **Data Provider** settings, select **BCS**.
3. Paste your refresh token into the masked input field.
4. Tap **Save token**.
5. Optionally tap **Test** to verify connectivity.

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

## Limitations

- BCS support is **experimental**.
- No orders, no trading, no portfolio operations — the app is read-only for market data only.
- Live polling is **disabled** in BCS mode (candles are loaded on demand / manual refresh).
- Watchlist quotes continue to use **MOEX** (BCS quote endpoint not yet mapped).
- If BCS fails and **Fallback to MOEX** is enabled, the chart loads MOEX data and shows a yellow warning: `BCS unavailable, using MOEX`.
- BCS candle field names and endpoint URLs are marked as `TODO: verify` in the source code and must be confirmed against the BCS API documentation before production use.

---

## Fallback behavior

When **Fallback to MOEX** is enabled (default):
- If BCS candle load fails for any reason (auth error, network error, rate limit), the chart automatically loads MOEX data.
- A compact yellow warning is shown: **BCS unavailable, using MOEX**.
- The footer shows: **Data: BCS→MOEX**.

When fallback is disabled:
- BCS failures show a readable error message.
- Existing chart data (if any) is preserved — no black/blank screen.

---

## BCS API verification checklist

Before enabling BCS in a production build, verify the following from BCS API documentation:

- [ ] Token endpoint URL (`BCS_TOKEN_URL` in `bcsAuth.ts`)
- [ ] Auth request body shape: `grant_type`, `client_id`, other fields
- [ ] Auth response field names: `access_token`, `expires_in`
- [ ] Candle endpoint URL (`BCS_CANDLES_URL` in `bcsMarketData.ts`)
- [ ] Candle query parameter names: `instrumentId`, `interval`, `from`, `to`, `count`
- [ ] Candle interval values: `M5`, `M15`, `H1`, `D1` (or BCS-specific naming)
- [ ] Candle response envelope: direct array or `{ data: [...] }` or other
- [ ] Candle field names: `time`/`t`, `open`/`o`, `high`/`h`, `low`/`l`, `close`/`c`, `volume`/`v`
- [ ] Timestamp format: ISO 8601 with timezone or epoch milliseconds
- [ ] Rate limit: confirm 10 RPS for market data endpoint
