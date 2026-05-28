// BCS authorization client.
// Exchanges a user-provided refresh token for a short-lived access token.
// Access token is cached in session memory only; never logged or persisted.

import { storeAccessToken, getAccessToken, getRefreshToken } from '../security/tokenStorage';

// ---------------------------------------------------------------------------
// TODO: Verify all BCS endpoint values and request shape against BCS API docs
// or your BCS Postman collection before enabling BCS in production.
// ---------------------------------------------------------------------------

// TODO: Confirm exact BCS OAuth token endpoint URL.
const BCS_TOKEN_URL = 'https://api-gateway.bcs.ru/v1/oauth/token';

// TODO: Confirm whether BCS requires a client_id for refresh-token grants.
const BCS_CLIENT_ID = 'mobile_app';

// ---------------------------------------------------------------------------
// Error types
// ---------------------------------------------------------------------------

export type BcsAuthError =
  | 'invalid_token'   // 401/403 or missing access_token in response
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

  // TODO: Verify exact request body shape against BCS API documentation.
  let resp: Response;
  try {
    resp = await fetch(BCS_TOKEN_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        grant_type: 'refresh_token',
        refresh_token: refreshToken,
        client_id: BCS_CLIENT_ID,
      }),
    });
  } catch {
    throw new BcsAuthException('network_error', 'Network error connecting to BCS auth server.');
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
  // TODO: Confirm BCS response field names (access_token, expires_in).
  const accessToken = typeof obj.access_token === 'string' ? obj.access_token : null;
  const expiresIn   = typeof obj.expires_in   === 'number' ? obj.expires_in   : 86400;

  if (!accessToken) {
    throw new BcsAuthException('unknown', 'BCS auth response missing access_token field.');
  }

  // Token is stored in memory only — not logged, not persisted.
  storeAccessToken(accessToken, expiresIn);
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
