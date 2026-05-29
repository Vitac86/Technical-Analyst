// BCS authorization client.
// Exchanges a refresh token for a short-lived access token.
// Tokens are cached in session memory only; never logged or persisted.

import {
  getAccessToken,
  getRefreshTokenWithMeta,
  rememberBcsClientIdHint,
  storeAccessToken,
  storeRefreshToken,
} from '../security/tokenStorage';
import type { BcsClientId } from '../security/tokenStorage';

const BCS_BASE_URL = 'https://be.broker.ru';
const BCS_TOKEN_URL =
  `${BCS_BASE_URL}/trade-api-keycloak/realms/tradeapi/protocol/openid-connect/token`;
const DEFAULT_BCS_CLIENT_ID: BcsClientId = 'trade-api-read';

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

type AuthErrorBody = {
  errorCode: string;
  description: string;
  message: string;
};

type AuthExchangeSuccess = {
  ok: true;
  accessToken: string;
  expiresIn: number;
  newRefreshToken: string | null;
};

type AuthExchangeFailure = {
  ok: false;
  status: number;
  clientMismatchLike: boolean;
  exception: BcsAuthException;
};

type AuthExchangeResult = AuthExchangeSuccess | AuthExchangeFailure;

// ---------------------------------------------------------------------------
// Token exchange helpers
// ---------------------------------------------------------------------------

function alternateClientId(clientId: BcsClientId): BcsClientId {
  return clientId === 'trade-api-read' ? 'trade-api-write' : 'trade-api-read';
}

async function readAuthErrorBody(resp: Response): Promise<AuthErrorBody> {
  let raw = '';
  try {
    raw = await resp.text();
  } catch {
    raw = '';
  }

  let body: Record<string, unknown> | null = null;
  if (raw) {
    try {
      const parsed = JSON.parse(raw) as unknown;
      if (parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed)) {
        body = parsed as Record<string, unknown>;
      }
    } catch {
      body = null;
    }
  }

  const error = body?.error;
  const description = body?.error_description;
  const message = body?.message;

  return {
    errorCode: typeof error === 'string' ? error : '',
    description: typeof description === 'string' ? description : '',
    message: typeof message === 'string' ? message : '',
  };
}

function isClientMismatchLike(status: number, err: AuthErrorBody): boolean {
  if (status !== 400 && status !== 401) return false;

  const code = err.errorCode.toLowerCase();
  if (code === 'invalid_client' || code === 'unauthorized_client') return true;

  const text = `${err.errorCode} ${err.description} ${err.message}`.toLowerCase();
  if (text.includes('client_id')) return true;
  if (!text.includes('client')) return false;

  return (
    text.includes('match') ||
    text.includes('mismatch') ||
    text.includes('azp') ||
    text.includes('authorized') ||
    text.includes('unauthorized') ||
    text.includes('not allowed')
  );
}

function authFailureMessage(status: number, err: AuthErrorBody): string {
  if (status === 400) {
    if (err.errorCode === 'invalid_grant') {
      return 'BCS refresh token is invalid, expired, or does not match the selected client.';
    }
    return `BCS auth failed: HTTP 400${err.errorCode ? ` (${err.errorCode})` : ''}.`;
  }

  if (status === 401 || status === 403) {
    return 'BCS token rejected (401/403). Please paste a new refresh token in Settings.';
  }

  return `BCS auth failed: HTTP ${status}`;
}

function authFailureKind(status: number): BcsAuthError {
  if (status === 400 || status === 401 || status === 403) return 'invalid_token';
  return 'unknown';
}

function shouldRetryWithAlternateClient(
  failure: AuthExchangeFailure,
  hasClientIdHint: boolean,
): boolean {
  if (failure.status !== 400 && failure.status !== 401) return false;
  return failure.clientMismatchLike || !hasClientIdHint;
}

async function exchangeRefreshToken(
  refreshToken: string,
  clientId: BcsClientId,
): Promise<AuthExchangeResult> {
  let resp: Response;
  try {
    resp = await fetch(BCS_TOKEN_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Accept': 'application/json',
      },
      body: new URLSearchParams({
        client_id: clientId,
        grant_type: 'refresh_token',
        refresh_token: refreshToken,
      }),
    });
  } catch {
    throw new BcsAuthException('network_error', 'Network error connecting to BCS auth server.');
  }

  if (resp.status === 429) {
    throw new BcsAuthException('rate_limited', 'BCS auth rate limited (429). Retry later.');
  }

  if (!resp.ok) {
    const errBody = await readAuthErrorBody(resp);
    return {
      ok: false,
      status: resp.status,
      clientMismatchLike: isClientMismatchLike(resp.status, errBody),
      exception: new BcsAuthException(
        authFailureKind(resp.status),
        authFailureMessage(resp.status, errBody),
      ),
    };
  }

  let json: unknown;
  try {
    json = await resp.json();
  } catch {
    throw new BcsAuthException('unknown', 'BCS auth response was not valid JSON.');
  }

  const obj = json as Record<string, unknown>;
  const accessToken = typeof obj.access_token === 'string' ? obj.access_token : null;
  const expiresIn = typeof obj.expires_in === 'number' ? obj.expires_in : 86400;

  if (!accessToken) {
    throw new BcsAuthException('unknown', 'BCS auth response missing access_token field.');
  }

  return {
    ok: true,
    accessToken,
    expiresIn,
    newRefreshToken: typeof obj.refresh_token === 'string' ? obj.refresh_token : null,
  };
}

// ---------------------------------------------------------------------------
// Public token API
// ---------------------------------------------------------------------------

export async function getBcsAccessToken(): Promise<string> {
  const cached = getAccessToken();
  if (cached) return cached;

  const refreshMeta = getRefreshTokenWithMeta();
  if (!refreshMeta.token) {
    throw new BcsAuthException(
      'invalid_token',
      'No BCS refresh token stored. Paste a token in Settings.',
    );
  }

  const firstClientId = refreshMeta.clientIdHint ?? DEFAULT_BCS_CLIENT_ID;
  const firstResult = await exchangeRefreshToken(refreshMeta.token, firstClientId);

  let result = firstResult;
  let usedClientId = firstClientId;

  if (!firstResult.ok) {
    const retryClientId = alternateClientId(firstClientId);
    if (shouldRetryWithAlternateClient(firstResult, refreshMeta.clientIdHint !== null)) {
      const retryResult = await exchangeRefreshToken(refreshMeta.token, retryClientId);
      result = retryResult;
      usedClientId = retryClientId;
    }
  }

  if (!result.ok) {
    throw result.exception;
  }

  // If BCS returned a rotated refresh token, keep it in session memory only.
  // Store it before the access token because refresh-token updates clear
  // stale cached access tokens.
  if (result.newRefreshToken) {
    storeRefreshToken(result.newRefreshToken, usedClientId);
  } else {
    rememberBcsClientIdHint(refreshMeta.source, usedClientId);
  }

  storeAccessToken(result.accessToken, result.expiresIn);
  return result.accessToken;
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
