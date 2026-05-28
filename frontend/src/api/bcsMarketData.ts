// BCS historical candle client.
// Normalizes BCS candle responses into MoexCandle shape so chart code is unchanged.
// No candle caching or persistence — candles live in React state only.

import type { MoexCandle } from './moexDirect';
import { aggregateCandles } from './moexDirect';
import { getBcsAccessToken, BcsAuthException } from './bcsAuth';

// ---------------------------------------------------------------------------
// TODO: Verify all BCS endpoint values and request/response shapes against
// BCS API documentation or your BCS Postman collection before use.
// ---------------------------------------------------------------------------

// TODO: Confirm exact BCS candle endpoint URL.
const BCS_CANDLES_URL = 'https://api-gateway.bcs.ru/v1/candles-chart';

// TODO: Confirm BCS interval parameter values (M5 / M15 / H1 / D1 or other naming).
const BCS_INTERVAL_MAP: Record<string, string> = {
  '5m':  'M5',  // TODO: verify
  '15m': 'M15', // TODO: verify
  '1h':  'H1',  // TODO: verify
  '4h':  'H1',  // Fetch H1, aggregate to 4h client-side (BCS may not support H4)
  '1d':  'D1',  // TODO: verify
};

const AGGREGATE_4H_MINUTES = 240;
const BCS_MAX_BARS = 1440;
// Pace sequential chunk requests to stay within ~10 RPS market data limit.
const BCS_REQUEST_DELAY_MS = 150;

// ---------------------------------------------------------------------------
// Error types
// ---------------------------------------------------------------------------

export type BcsLoadError = 'auth_error' | 'rate_limited' | 'network_error' | 'unknown';

export class BcsMarketDataException extends Error {
  constructor(public readonly kind: BcsLoadError, message: string) {
    super(message);
    this.name = 'BcsMarketDataException';
  }
}

// ---------------------------------------------------------------------------
// Raw candle normalizer
// ---------------------------------------------------------------------------

type RawBcsCandle = Record<string, unknown>;

function readNum(raw: RawBcsCandle, keys: string[]): number | null {
  for (const key of keys) {
    const v = raw[key];
    if (typeof v === 'number' && Number.isFinite(v)) return v;
    if (typeof v === 'string') {
      const n = Number(v);
      if (Number.isFinite(n)) return n;
    }
  }
  return null;
}

function rawTimeToMoscow(raw: RawBcsCandle): string | null {
  // TODO: Confirm BCS timestamp field name and format.
  // Try common field names: time, t, date, begin, timestamp.
  const val = raw.time ?? raw.t ?? raw.date ?? raw.begin ?? raw.timestamp;
  if (val == null) return null;
  try {
    const d = typeof val === 'number' ? new Date(val) : new Date(String(val));
    if (isNaN(d.getTime())) return null;
    // Shift to Moscow time (UTC+3) to match MoexCandle "YYYY-MM-DD HH:MM:SS" convention.
    const msk = new Date(d.getTime() + 3 * 3600 * 1000);
    const p = (n: number) => String(n).padStart(2, '0');
    return (
      `${msk.getUTCFullYear()}-${p(msk.getUTCMonth() + 1)}-${p(msk.getUTCDate())} ` +
      `${p(msk.getUTCHours())}:${p(msk.getUTCMinutes())}:${p(msk.getUTCSeconds())}`
    );
  } catch {
    return null;
  }
}

function parseBcsCandleArray(data: unknown): MoexCandle[] {
  if (!Array.isArray(data)) return [];
  const result: MoexCandle[] = [];
  for (const item of data) {
    if (typeof item !== 'object' || item === null) continue;
    const raw = item as RawBcsCandle;
    const begin = rawTimeToMoscow(raw);
    if (!begin) continue;
    // TODO: Confirm OHLCV field names. Try long (open, high, low, close, volume) and short (o, h, l, c, v).
    const open  = readNum(raw, ['open',  'o']);
    const high  = readNum(raw, ['high',  'h']);
    const low   = readNum(raw, ['low',   'l']);
    const close = readNum(raw, ['close', 'c']);
    const vol   = readNum(raw, ['volume', 'v', 'vol']);
    if (open == null || high == null || low == null || close == null) continue;
    result.push({ begin, open, high, low, close, volume: vol ?? 0 });
  }
  return result;
}

// ---------------------------------------------------------------------------
// Single chunk fetch
// ---------------------------------------------------------------------------

async function fetchBcsCandleChunk(
  ticker: string,
  bcsInterval: string,
  fromIso: string,
  toIso: string,
): Promise<MoexCandle[]> {
  let token: string;
  try {
    token = await getBcsAccessToken();
  } catch (err) {
    const msg = err instanceof Error ? err.message : 'BCS auth failed before candle request.';
    throw new BcsMarketDataException('auth_error', msg);
  }

  // TODO: Confirm BCS query parameter names and value formats.
  const url = new URL(BCS_CANDLES_URL);
  url.searchParams.set('instrumentId', ticker);             // TODO: verify param name
  url.searchParams.set('interval',     bcsInterval);        // TODO: verify param name
  url.searchParams.set('from',         fromIso);            // TODO: verify date format
  url.searchParams.set('to',           toIso);              // TODO: verify param name
  url.searchParams.set('count',        String(BCS_MAX_BARS)); // TODO: verify param name

  let resp: Response;
  try {
    resp = await fetch(url.toString(), {
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: 'application/json',
      },
    });
  } catch {
    throw new BcsMarketDataException('network_error', 'Network error fetching BCS candles.');
  }

  if (resp.status === 401 || resp.status === 403) {
    throw new BcsMarketDataException('auth_error', 'BCS candle request rejected (401/403). Token may be expired.');
  }
  if (resp.status === 429) {
    throw new BcsMarketDataException('rate_limited', 'BCS rate limited (429). Retrying later.');
  }
  if (!resp.ok) {
    throw new BcsMarketDataException('unknown', `BCS candles failed: HTTP ${resp.status}`);
  }

  let json: unknown;
  try {
    json = await resp.json();
  } catch {
    throw new BcsMarketDataException('unknown', 'BCS candles response was not valid JSON.');
  }

  // TODO: Confirm BCS response envelope. Try common patterns.
  let candleData: unknown;
  if (Array.isArray(json)) {
    candleData = json;
  } else if (typeof json === 'object' && json !== null) {
    const obj = json as Record<string, unknown>;
    candleData = obj.data ?? obj.candles ?? obj.result ?? obj.items ?? [];
  } else {
    candleData = [];
  }

  return parseBcsCandleArray(candleData);
}

// ---------------------------------------------------------------------------
// Public load function
// ---------------------------------------------------------------------------

function sleep(ms: number): Promise<void> {
  return new Promise(r => setTimeout(r, ms));
}

export async function loadBcsCandles(
  ticker: string,
  timeframe: string,
  from: string,
  till: string,
): Promise<MoexCandle[]> {
  const bcsInterval = BCS_INTERVAL_MAP[timeframe];
  if (!bcsInterval) {
    throw new BcsMarketDataException('unknown', `Unsupported BCS timeframe: ${timeframe}`);
  }

  const fromDate = new Date(from + 'T00:00:00Z');
  const tillDate = new Date(till + 'T23:59:59Z');

  const allCandles: MoexCandle[] = [];
  const seen = new Set<string>();
  let currentFrom = fromDate;

  while (currentFrom <= tillDate) {
    const chunk = await fetchBcsCandleChunk(
      ticker,
      bcsInterval,
      currentFrom.toISOString(),
      tillDate.toISOString(),
    );

    if (chunk.length === 0) break;

    for (const c of chunk) {
      if (!seen.has(c.begin)) {
        seen.add(c.begin);
        allCandles.push(c);
      }
    }

    if (chunk.length < BCS_MAX_BARS) break;

    // Advance 1 minute past the last returned candle to fetch the next chunk
    const lastBegin = chunk[chunk.length - 1].begin;
    currentFrom = new Date(lastBegin.replace(' ', 'T') + 'Z');
    currentFrom.setTime(currentFrom.getTime() + 60_000);

    await sleep(BCS_REQUEST_DELAY_MS);
  }

  allCandles.sort((a, b) => (a.begin < b.begin ? -1 : a.begin > b.begin ? 1 : 0));

  // 4h: BCS provides H1; aggregate client-side, same logic as moexDirect
  if (timeframe === '4h') {
    return aggregateCandles(allCandles, AGGREGATE_4H_MINUTES);
  }

  return allCandles;
}
