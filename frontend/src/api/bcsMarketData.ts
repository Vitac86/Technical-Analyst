// BCS historical candle client.
// Normalizes BCS candle responses into MoexCandle shape so chart code is unchanged.
// No candle caching or persistence — candles live in React state only.

import type { MoexCandle } from './moexDirect';
import { getBcsAccessToken } from './bcsAuth';

const BCS_CANDLES_URL =
  'https://be.broker.ru/trade-api-market-data-connector/api/v1/candles-chart';

// Timeframe names confirmed via BCS Postman collection.
const BCS_INTERVAL_MAP: Record<string, string> = {
  '5m':  'M5',
  '15m': 'M15',
  '1h':  'H1',
  '4h':  'H4',
  '1d':  'D',
};

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

// BCS returns UTC timestamps; shift +3h to match MOEX's Moscow-time display convention.
function isoToMoscowBegin(raw: RawBcsCandle): string | null {
  const val = raw.time ?? raw.t ?? raw.date ?? raw.begin ?? raw.timestamp;
  if (val == null) return null;
  try {
    const d = typeof val === 'number' ? new Date(val) : new Date(String(val));
    if (isNaN(d.getTime())) return null;
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

function parseBcsBars(bars: unknown): MoexCandle[] {
  if (!Array.isArray(bars)) return [];
  const result: MoexCandle[] = [];
  for (const item of bars) {
    if (typeof item !== 'object' || item === null) continue;
    const raw = item as RawBcsCandle;
    const begin = isoToMoscowBegin(raw);
    if (!begin) continue;
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
// Fetch
// ---------------------------------------------------------------------------

async function fetchBcsCandles(
  ticker: string,
  classCode: string,
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

  const url = new URL(BCS_CANDLES_URL);
  url.searchParams.set('ticker',    ticker);
  url.searchParams.set('classCode', classCode);
  url.searchParams.set('startDate', fromIso);
  url.searchParams.set('endDate',   toIso);
  url.searchParams.set('timeFrame', bcsInterval);

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

  // BCS response shape: { ticker, classCode, startDate, endDate, timeFrame, bars: [...] }
  const bars = (typeof json === 'object' && json !== null)
    ? (json as Record<string, unknown>).bars
    : null;

  return parseBcsBars(bars);
}

// ---------------------------------------------------------------------------
// Public load function
// ---------------------------------------------------------------------------

export async function loadBcsCandles(
  ticker: string,
  classCode: string,
  timeframe: string,
  from: string,
  till: string,
): Promise<MoexCandle[]> {
  const bcsInterval = BCS_INTERVAL_MAP[timeframe];
  if (!bcsInterval) {
    throw new BcsMarketDataException('unknown', `Unsupported BCS timeframe: ${timeframe}`);
  }

  const fromIso = new Date(from + 'T00:00:00Z').toISOString();
  const tillIso = new Date(till + 'T23:59:59Z').toISOString();

  const candles = await fetchBcsCandles(ticker, classCode, bcsInterval, fromIso, tillIso);
  // BCS returns bars newest-first; sort ascending for the chart.
  candles.sort((a, b) => (a.begin < b.begin ? -1 : a.begin > b.begin ? 1 : 0));
  return candles;
}
