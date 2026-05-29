// BCS token storage — session memory only.
// BCS tokens are NOT stored in localStorage, IndexedDB, or any persistent store.
// Token is lost on app restart/reload; user must re-enter it each session.
//
// A build-time default refresh token may be provided via the env variable
// VITE_DEFAULT_BCS_REFRESH_TOKEN (defined in `.env.local`, which is gitignored).
// The default token is held in module-private memory only and is used as a
// fallback when no session override has been set. It is never logged, never
// written to localStorage/IndexedDB, never echoed back to the UI in full.
// If the user enters their own token, the user token overrides the default
// for the current session.

const _defaultRefreshToken: string = (() => {
  const raw = import.meta.env.VITE_DEFAULT_BCS_REFRESH_TOKEN;
  if (typeof raw !== "string") return "";
  return raw.trim();
})();

let _sessionRefreshToken: string | null = null;
let _accessToken: string | null = null;
let _accessTokenExpiry = 0; // Unix ms

export type TokenSource = "session" | "default" | "none";

export function storeRefreshToken(token: string): void {
  _sessionRefreshToken = token.trim();
}

export function getRefreshToken(): string | null {
  if (_sessionRefreshToken && _sessionRefreshToken.length > 0) {
    return _sessionRefreshToken;
  }
  if (_defaultRefreshToken.length > 0) {
    return _defaultRefreshToken;
  }
  return null;
}

/**
 * Clears the user-entered session override and any cached access token.
 * The build-time default token (if present) remains available.
 */
export function clearTokens(): void {
  _sessionRefreshToken = null;
  _accessToken = null;
  _accessTokenExpiry = 0;
}

export function hasRefreshToken(): boolean {
  return getRefreshToken() !== null;
}

/**
 * Indicates which token would currently be used: a user-entered session
 * override, the build-time default, or none. Used by Settings UI for a
 * single-line status badge — never returns the token value itself.
 */
export function getTokenSource(): TokenSource {
  if (_sessionRefreshToken && _sessionRefreshToken.length > 0) return "session";
  if (_defaultRefreshToken.length > 0) return "default";
  return "none";
}

/**
 * Whether a build-time default token is bundled. Used by Settings UI to show
 * a "default token available in this build" hint. Never returns the value.
 */
export function hasDefaultToken(): boolean {
  return _defaultRefreshToken.length > 0;
}

/**
 * Whether the user has entered their own session override on top of the
 * default. Used by Settings UI to label Clear correctly.
 */
export function hasSessionOverride(): boolean {
  return _sessionRefreshToken !== null && _sessionRefreshToken.length > 0;
}

export function storeAccessToken(token: string, expiresInSeconds: number): void {
  _accessToken = token;
  // 60-second safety margin before declared expiry
  _accessTokenExpiry = Date.now() + (expiresInSeconds - 60) * 1000;
}

export function getAccessToken(): string | null {
  if (!_accessToken) return null;
  if (Date.now() >= _accessTokenExpiry) {
    _accessToken = null;
    return null;
  }
  return _accessToken;
}

export function isAccessTokenExpired(): boolean {
  return !_accessToken || Date.now() >= _accessTokenExpiry;
}
