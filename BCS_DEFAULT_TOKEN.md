# Default BCS Read-Only Token (Private APK Builds)

Technical Analyst supports an optional **build-time default BCS refresh
token** so that a private APK build can connect to BCS Broker immediately
after install — no manual paste required.

This is intended for **personal / private** APK builds only. The token must
be a **read-only** refresh token. Do **not** use a token with trading
permissions, and do **not** commit the token to GitHub.

## Token-resolution priority

At runtime, the app picks a BCS refresh token using this order:

1. **Custom session token** — if you pasted a token in `Settings → Data`, it
   is used (held in memory only, cleared on app restart).
2. **App default token** — if the build was made with
   `VITE_DEFAULT_BCS_REFRESH_TOKEN` set, it is used as a fallback.
3. **None** — the app surfaces `BCS token required` and asks you to paste
   one in Settings.

`Settings → Data` shows the active source as a small badge:

- `Token source: custom session token`
- `Token source: app default token`
- `Token source: none`

It also shows the OAuth client safely:

- `Client: trade-api-read`
- `Client: trade-api-write`
- `Client: auto` when a pasted token does not expose a usable JWT `azp`

The token value itself is never shown.

## How to embed a token into a private build

1. Create `frontend/.env.local` (already gitignored via `frontend/.gitignore`).
2. Add the variable:

   ```env
   VITE_DEFAULT_BCS_REFRESH_TOKEN=<your_read_only_refresh_token>
   VITE_DEFAULT_BCS_CLIENT_ID=trade-api-read
   ```

   `VITE_DEFAULT_BCS_CLIENT_ID` is optional; if omitted, the app defaults the
   bundled token to `trade-api-read`.

3. Rebuild and assemble the APK. Vite embeds `.env.local` values at build time,
   so rebuilding is required after every token/client change:

   ```bat
   cd frontend
   npm.cmd run build
   npm.cmd run android:sync
   cd android
   .\gradlew.bat assembleDebug
   ```

   The APK at `frontend/android/app/build/outputs/apk/debug/app-debug.apk`
   now contains the token, embedded into the JavaScript bundle at build time.

That's it — BCS candles, quotes, and order book will work immediately after
install.

## Safety rules

- **Never commit `.env.local`.** It is already in `frontend/.gitignore`.
- **Use a read-only refresh token only.** A trading-scoped token must not be
  used here; the app does not expose trading endpoints, but a leaked token
  with trading scope could be abused if the APK ever leaves your control.
- **Do not paste the token into:** code, comments, docs, test fixtures,
  example files, console output, or commit messages.
- **The app never stores the token in `localStorage` or `IndexedDB`.** It is
  held in module-private memory only and is never echoed back to the UI in
  full.
- **The app never logs the token.** Errors that mention BCS auth show only a
  short user-facing message ("BCS token required for order book") — the
  token itself does not appear in the message.
- **Public GitHub releases should not include personal tokens.** Either ship
  a build without `VITE_DEFAULT_BCS_REFRESH_TOKEN`, or only distribute the
  built APK privately.
- **If a token is exposed, rotate it in the BCS account immediately.** The
  default token persists in the bundled JavaScript of any APK built with it,
  so a leaked APK can be inspected to recover the token.

## Behaviour of "Clear" in Settings

The Clear button in `Settings → Data` clears only the custom session
override and the cached short-lived access token. If a build-time default
exists, the app falls back to it and the token-source badge switches from
`custom session token` to `app default token`.
