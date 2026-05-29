// BCS token storage - session memory only.
// BCS tokens are NOT stored in localStorage, IndexedDB, or any persistent store.
// Token is lost on app restart/reload; user must re-enter it each session.
//
// A build-time default refresh token may be provided via the env variable
// VITE_DEFAULT_BCS_REFRESH_TOKEN (defined in `.env.local`, which is gitignored).
// VITE_DEFAULT_BCS_CLIENT_ID may optionally pin the matching OAuth client id.
// The default token is held in module-private memory only and is used as a
// fallback when no session override has been set. It is never logged, never
// written to localStorage/IndexedDB, never echoed back to the UI in full.
// If the user enters their own token, the user token overrides the default
// for the current session.

export type BcsClientId = "trade-api-read" | "trade-api-write";
export type TokenSource = "session" | "default" | "none";

export type RefreshTokenMeta = {
  token: string | null;
  source: TokenSource;
  clientIdHint: BcsClientId | null;
};

const _defaultRefreshToken: string = (() => {
  const raw = import.meta.env.VITE_DEFAULT_BCS_REFRESH_TOKEN;
  if (typeof raw !== "string") return "";
  return raw.trim();
})();

const _configuredDefaultClientId: BcsClientId | null = normalizeBcsClientId(
  import.meta.env.VITE_DEFAULT_BCS_CLIENT_ID,
);

const _initialDefaultClientIdHint: BcsClientId | null =
  _defaultRefreshToken.length > 0
    ? _configuredDefaultClientId
      ?? inferBcsClientIdFromRefreshToken(_defaultRefreshToken)
      ?? "trade-api-read"
    : null;

let _sessionRefreshToken: string | null = null;
let _sessionClientIdHint: BcsClientId | null = null;
let _defaultClientIdHint: BcsClientId | null = _initialDefaultClientIdHint;
let _accessToken: string | null = null;
let _accessTokenExpiry = 0; // Unix ms

function normalizeBcsClientId(value: unknown): BcsClientId | null {
  if (value === "trade-api-read" || value === "trade-api-write") return value;
  return null;
}

function clearAccessToken(): void {
  _accessToken = null;
  _accessTokenExpiry = 0;
}

function decodeBase64Url(input: string): string | null {
  if (typeof globalThis.atob !== "function") return null;

  const base64 = input.replace(/-/g, "+").replace(/_/g, "/");
  if (base64.length % 4 === 1) return null;

  try {
    const padded = base64 + "=".repeat((4 - (base64.length % 4)) % 4);
    const binary = globalThis.atob(padded);
    const bytes = Uint8Array.from(binary, (ch) => ch.charCodeAt(0));
    return new TextDecoder().decode(bytes);
  } catch {
    return null;
  }
}

export function inferBcsClientIdFromRefreshToken(token: string): BcsClientId | null {
  const payloadPart = token.split(".")[1];
  if (!payloadPart) return null;

  try {
    const decoded = decodeBase64Url(payloadPart);
    if (!decoded) return null;

    const payload = JSON.parse(decoded) as unknown;
    if (payload === null || typeof payload !== "object" || Array.isArray(payload)) {
      return null;
    }

    const azp = (payload as Record<string, unknown>).azp;
    return normalizeBcsClientId(azp);
  } catch {
    return null;
  }
}

export function storeRefreshToken(token: string, clientIdHint?: BcsClientId | null): void {
  const trimmed = token.trim();
  _sessionRefreshToken = trimmed;
  _sessionClientIdHint =
    clientIdHint ?? inferBcsClientIdFromRefreshToken(trimmed);
  clearAccessToken();
}

export function getRefreshToken(): string | null {
  return getRefreshTokenWithMeta().token;
}

export function getRefreshTokenWithMeta(): RefreshTokenMeta {
  if (_sessionRefreshToken && _sessionRefreshToken.length > 0) {
    return {
      token: _sessionRefreshToken,
      source: "session",
      clientIdHint: _sessionClientIdHint,
    };
  }
  if (_defaultRefreshToken.length > 0) {
    return {
      token: _defaultRefreshToken,
      source: "default",
      clientIdHint: _defaultClientIdHint,
    };
  }
  return { token: null, source: "none", clientIdHint: null };
}

export function getBcsClientIdHint(): BcsClientId | null {
  return getRefreshTokenWithMeta().clientIdHint;
}

export function rememberBcsClientIdHint(
  source: TokenSource,
  clientId: BcsClientId,
): void {
  if (source === "session" && _sessionRefreshToken && _sessionRefreshToken.length > 0) {
    _sessionClientIdHint = clientId;
  } else if (source === "default" && _defaultRefreshToken.length > 0) {
    _defaultClientIdHint = clientId;
  }
}

/**
 * Clears the user-entered session override and any cached access token.
 * The build-time default token (if present) remains available.
 */
export function clearTokens(): void {
  _sessionRefreshToken = null;
  _sessionClientIdHint = null;
  clearAccessToken();
}

export function hasRefreshToken(): boolean {
  return getRefreshToken() !== null;
}

/**
 * Indicates which token would currently be used: a user-entered session
 * override, the build-time default, or none. Used by Settings UI for a
 * single-line status badge - never returns the token value itself.
 */
export function getTokenSource(): TokenSource {
  return getRefreshTokenWithMeta().source;
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
    clearAccessToken();
    return null;
  }
  return _accessToken;
}

export function isAccessTokenExpired(): boolean {
  return !_accessToken || Date.now() >= _accessTokenExpiry;
}
