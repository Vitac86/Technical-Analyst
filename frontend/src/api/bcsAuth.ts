// BCS authorization client.
// Exchanges a user-provided refresh token for a short-lived access token.
// Access token is cached in session memory only; never logged or persisted.

import { storeAccessToken, getAccessToken, getRefreshToken, storeRefreshToken } from '../security/tokenStorage';

const BCS_BASE_URL = 'https://be.broker.ru';
const BCS_TOKEN_URL =
  `${BCS_BASE_URL}/trade-api-keycloak/realms/tradeapi/protocol/openid-connect/token`;

// ---------------------------------------------------------------------------
// Error types
// ---------------------------------------------------------------------------

export type BcsAuthError =
  | 'invalid_token'   // 400 invalid_grant, 401/403, or missing access_token
  | 'network_error'   // fetch threw
  | 'rate_limited'    // 429
  | 'unknown';

export class BcsAuthException extends Error {
  constructor(public readonly kind: BcsAuthError, message: string) {
    super(message);
    this.name = 'BcsAuthException';
  }
}

// ---------------------------------------------------------------------------
// Token exchange
// ---------------------------------------------------------------------------

export async function getBcsAccessToken(): Promise<string> {
  const cached = getAccessToken();
  if (cached) return cached;

  const refreshToken = getRefreshToken();
  if (!refreshToken) {
    throw new BcsAuthException(
      'invalid_token',
      'No BCS refresh token stored. Paste a token in Settings.',
    );
  }

  let resp: Response;
  try {
    resp = await fetch(BCS_TOKEN_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Accept': 'application/json',
      },
      body: new URLSearchParams({
        client_id: 'trade-api-write',
        grant_type: 'refresh_token',
        refresh_token: refreshToken,
      }),
    });
  } catch {
    throw new BcsAuthException('network_error', 'Network error connecting to BCS auth server.');
  }

  if (resp.status === 400) {
    let errorCode = '';
    try {
      const errBody = await resp.json() as Record<string, unknown>;
      errorCode = typeof errBody.error === 'string' ? errBody.error : '';
    } catch { /* ignore parse error */ }
    const message = errorCode === 'invalid_grant'
      ? 'BCS refresh token is invalid or expired. Paste a new token.'
      : `BCS auth failed: HTTP 400${errorCode ? ` (${errorCode})` : ''}.`;
    throw new BcsAuthException('invalid_token', message);
  }

  if (resp.status === 401 || resp.status === 403) {
    throw new BcsAuthException(
      'invalid_token',
      'BCS token rejected (401/403). Please paste a new refresh token in Settings.',
    );
  }

  if (resp.status === 429) {
    throw new BcsAuthException('rate_limited', 'BCS auth rate limited (429). Retry later.');
  }

  if (!resp.ok) {
    throw new BcsAuthException('unknown', `BCS auth failed: HTTP ${resp.status}`);
  }

  let json: unknown;
  try {
    json = await resp.json();
  } catch {
    throw new BcsAuthException('unknown', 'BCS auth response was not valid JSON.');
  }

  const obj = json as Record<string, unknown>;
  const accessToken = typeof obj.access_token === 'string' ? obj.access_token : null;
  const expiresIn   = typeof obj.expires_in   === 'number' ? obj.expires_in   : 86400;

  if (!accessToken) {
    throw new BcsAuthException('unknown', 'BCS auth response missing access_token field.');
  }

  // Token is stored in memory only — not logged, not persisted.
  storeAccessToken(accessToken, expiresIn);

  // If BCS returned a rotated refresh token, update session memory.
  const newRefresh = typeof obj.refresh_token === 'string' ? obj.refresh_token : null;
  if (newRefresh) {
    storeRefreshToken(newRefresh);
  }

  return accessToken;
}

// ---------------------------------------------------------------------------
// Connection test
// ---------------------------------------------------------------------------

export type BcsTestResult =
  | { ok: true }
  | { ok: false; kind: BcsAuthError; message: string };

export async function testBcsConnection(): Promise<BcsTestResult> {
  try {
    await getBcsAccessToken();
    return { ok: true };
  } catch (err) {
    if (err instanceof BcsAuthException) {
      return { ok: false, kind: err.kind, message: err.message };
    }
    return {
      ok: false,
      kind: 'unknown',
      message: err instanceof Error ? err.message : 'Unknown error',
    };
  }
}
