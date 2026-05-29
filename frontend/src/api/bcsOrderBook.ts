// BCS order book API module.
// Fetches a snapshot of bids/asks for a given instrument from the BCS market data connector.
// Raw responses are never logged. The bearer token is never logged.

import { getBcsAccessToken } from './bcsAuth';

const ORDER_BOOK_URL =
  'https://be.broker.ru/trade-api-market-data-connector/api/v1/order-book';

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

export type OrderBookLevel = {
  price: number;
  quantity: number;
  value?: number;
};

export type OrderBookSnapshot = {
  ticker: string;
  classCode: string;
  depth: number;
  bids: OrderBookLevel[];
  asks: OrderBookLevel[];
  receivedAt: string;
};

// ---------------------------------------------------------------------------
// Error types (mirrors bcsAuth convention)
// ---------------------------------------------------------------------------

export type OrderBookErrorKind =
  | 'request_rejected'  // 400
  | 'auth_required'     // 401 / 403
  | 'rate_limited'      // 429
  | 'network_error'     // fetch threw
  | 'parse_error'       // response shape unrecognised
  | 'unknown';

export class OrderBookException extends Error {
  constructor(public readonly kind: OrderBookErrorKind, message: string) {
    super(message);
    this.name = 'OrderBookException';
  }
}

// ---------------------------------------------------------------------------
// Internal parser helpers
// ---------------------------------------------------------------------------

/** Coerce a raw value to a finite number, or return null. */
function toNumber(v: unknown): number | null {
  if (typeof v === 'number' && isFinite(v)) return v;
  if (typeof v === 'string') {
    const n = parseFloat(v);
    if (isFinite(n)) return n;
  }
  return null;
}

/** Parse a single raw level object into OrderBookLevel or null if invalid. */
function parseLevel(raw: unknown): OrderBookLevel | null {
  if (raw === null || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;

  // price: "price" or "p"
  const price = toNumber(r['price'] ?? r['p']);
  // quantity: "quantity", "qty", "size", or "q"
  const quantity = toNumber(r['quantity'] ?? r['qty'] ?? r['size'] ?? r['q']);

  if (price === null || quantity === null) return null;

  // value (optional): "value", "vol", or "volume"
  const rawValue = r['value'] ?? r['vol'] ?? r['volume'];
  const value = rawValue !== undefined ? toNumber(rawValue) ?? undefined : undefined;

  const level: OrderBookLevel = { price, quantity };
  if (value !== undefined) level.value = value;
  return level;
}

/** Parse an array of raw level objects, filtering out invalid entries. */
function parseLevels(arr: unknown): OrderBookLevel[] {
  if (!Array.isArray(arr)) return [];
  const result: OrderBookLevel[] = [];
  for (const item of arr) {
    const level = parseLevel(item);
    if (level !== null) result.push(level);
  }
  return result;
}

/**
 * Extract bid/ask arrays from the response body.
 * Supports the following shapes:
 *   { bids, asks }
 *   { buy, sell }
 *   { bid, offer }
 *   { data: { bids, asks } }
 * Throws OrderBookException('parse_error', ...) for unrecognised shapes.
 */
function extractSides(body: Record<string, unknown>): {
  rawBids: unknown;
  rawAsks: unknown;
} {
  // { data: { bids, asks } }
  if (
    body['data'] !== null &&
    typeof body['data'] === 'object' &&
    !Array.isArray(body['data'])
  ) {
    const data = body['data'] as Record<string, unknown>;
    if ('bids' in data && 'asks' in data) {
      return { rawBids: data['bids'], rawAsks: data['asks'] };
    }
  }

  // { bids, asks }
  if ('bids' in body && 'asks' in body) {
    return { rawBids: body['bids'], rawAsks: body['asks'] };
  }

  // { buy, sell }
  if ('buy' in body && 'sell' in body) {
    return { rawBids: body['buy'], rawAsks: body['sell'] };
  }

  // { bid, offer }
  if ('bid' in body && 'offer' in body) {
    return { rawBids: body['bid'], rawAsks: body['offer'] };
  }

  // Unknown shape — list safe top-level keys only (no values).
  const safeKeys = Object.keys(body).join(', ');
  throw new OrderBookException(
    'parse_error',
    `Unrecognised order book response shape. Top-level keys: [${safeKeys}]`,
  );
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Fetch a single order book snapshot for the given instrument.
 *
 * @param ticker    Instrument ticker symbol (e.g. "SBER").
 * @param classCode Exchange class code   (e.g. "TQBR").
 * @param depth     Number of price levels to request.
 */
export async function fetchOrderBook(
  ticker: string,
  classCode: string,
  depth: number,
): Promise<OrderBookSnapshot> {
  // Obtain bearer token (throws BcsAuthException on failure).
  const token = await getBcsAccessToken();

  const url = new URL(ORDER_BOOK_URL);
  url.searchParams.set('ticker', ticker);
  url.searchParams.set('classCode', classCode);
  url.searchParams.set('depth', String(depth));

  let resp: Response;
  try {
    resp = await fetch(url.toString(), {
      method: 'GET',
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: 'application/json',
      },
    });
  } catch {
    throw new OrderBookException(
      'network_error',
      'Network error fetching order book from BCS.',
    );
  }

  // --- HTTP error handling ---

  if (resp.status === 400) {
    let detail = '';
    try {
      const errBody = await resp.json() as Record<string, unknown>;
      if (typeof errBody['message'] === 'string') detail = errBody['message'];
      else if (typeof errBody['error'] === 'string') detail = errBody['error'];
    } catch { /* ignore */ }
    throw new OrderBookException(
      'request_rejected',
      `Order book request rejected by BCS (400)${detail ? `: ${detail}` : '.'}`,
    );
  }

  if (resp.status === 401 || resp.status === 403) {
    throw new OrderBookException(
      'auth_required',
      'BCS order book request unauthorised. Please re-authenticate in Settings.',
    );
  }

  if (resp.status === 429) {
    throw new OrderBookException(
      'rate_limited',
      'BCS order book rate limit reached (429). Please retry later.',
    );
  }

  if (!resp.ok) {
    throw new OrderBookException(
      'unknown',
      `BCS order book request failed: HTTP ${resp.status}.`,
    );
  }

  // --- Parse response body ---

  let json: unknown;
  try {
    json = await resp.json();
  } catch {
    throw new OrderBookException(
      'parse_error',
      'BCS order book response was not valid JSON.',
    );
  }

  if (json === null || typeof json !== 'object' || Array.isArray(json)) {
    throw new OrderBookException(
      'parse_error',
      'BCS order book response was not a JSON object.',
    );
  }

  const body = json as Record<string, unknown>;
  const { rawBids, rawAsks } = extractSides(body);

  const allBids = parseLevels(rawBids);
  const allAsks = parseLevels(rawAsks);

  // Sort: asks ascending by price, bids descending by price.
  allAsks.sort((a, b) => a.price - b.price);
  allBids.sort((a, b) => b.price - a.price);

  // Trim to requested depth.
  const bids = allBids.slice(0, depth);
  const asks = allAsks.slice(0, depth);

  return {
    ticker,
    classCode,
    depth,
    bids,
    asks,
    receivedAt: new Date().toISOString(),
  };
}
