// BCS token storage — session memory only.
// BCS tokens are NOT stored in localStorage, IndexedDB, or any persistent store.
// Token is lost on app restart/reload; user must re-enter it each session.

let _refreshToken: string | null = null;
let _accessToken: string | null = null;
let _accessTokenExpiry = 0; // Unix ms

export function storeRefreshToken(token: string): void {
  _refreshToken = token.trim();
}

export function getRefreshToken(): string | null {
  return _refreshToken;
}

export function clearTokens(): void {
  _refreshToken = null;
  _accessToken = null;
  _accessTokenExpiry = 0;
}

export function hasRefreshToken(): boolean {
  return _refreshToken !== null && _refreshToken.length > 0;
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
