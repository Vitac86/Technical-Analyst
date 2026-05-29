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

// Safe initial-load windows and lazy-load chunk sizes per timeframe.
// Keeps individual BCS requests small to avoid 400 range errors.
export const BCS_SAFE_WINDOW_DAYS: Record<string, number> = {
  '5m':  3,
  '15m': 10,
  '1h':  45,
  '4h':  180,
  '1d':  1500,
};

// ---------------------------------------------------------------------------
// Error types
// ---------------------------------------------------------------------------

export type BcsLoadError = 'auth_error' | 'invalid_request' | 'rate_limited' | 'network_error' | 'unknown';

export interface BcsApiError {
  provider: 'bcs';
  endpoint: 'auth' | 'candles-chart';
  status: number;
  type?: string;
  traceId?: string;
  safeMessage: string;
}

export class BcsMarketDataException extends Error {
  constructor(
    public readonly kind: BcsLoadError,
    message: string,
    public readonly apiError?: BcsApiError,
  ) {
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
// Fetch (single window)
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

  if (resp.status === 400) {
    let type: string | undefined;
    let traceId: string | undefined;
    try {
      const body = await resp.json() as Record<string, unknown>;
      type    = typeof body.type    === 'string' ? body.type    : undefined;
      traceId = typeof body.traceId === 'string' ? body.traceId : undefined;
    } catch { /* ignore parse failure */ }
    const apiError: BcsApiError = {
      provider: 'bcs',
      endpoint: 'candles-chart',
      status: 400,
      type,
      traceId,
      safeMessage: 'BCS rejected the candle request. Try a smaller range or another timeframe.',
    };
    throw new BcsMarketDataException('invalid_request', apiError.safeMessage, apiError);
  }

  if (resp.status === 401 || resp.status === 403) {
    throw new BcsMarketDataException(
      'auth_error',
      'BCS candle request rejected (401/403). Token may be expired.',
    );
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
// Public load functions
// ---------------------------------------------------------------------------

// Initial chart load — always capped to safe window to prevent large BCS requests.
// Large presets (e.g. 1Y on 5m) are silently trimmed; older bars load lazily via pan.
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

  const safeWindowDays = BCS_SAFE_WINDOW_DAYS[timeframe] ?? 3;
  const tillMs = new Date(till + 'T23:59:59Z').getTime();
  const fromMs = new Date(from + 'T00:00:00Z').getTime();
  const safeFromMs = tillMs - safeWindowDays * 86_400_000;
  const effectiveFromMs = Math.max(fromMs, safeFromMs);

  const fromIso = new Date(effectiveFromMs).toISOString();
  const tillIso = new Date(tillMs).toISOString();

  const candles = await fetchBcsCandles(ticker, classCode, bcsInterval, fromIso, tillIso);
  // BCS returns bars newest-first; sort ascending for the chart.
  candles.sort((a, b) => (a.begin < b.begin ? -1 : a.begin > b.begin ? 1 : 0));
  return candles;
}

// Lazy older-chunk loader. Fetches one safe window ending just before endDateExclusive.
// Called by MobileChartPage when the user pans left near the oldest loaded candle.
export async function loadBcsOlderChunk(
  ticker: string,
  classCode: string,
  timeframe: string,
  endDateExclusive: Date,
): Promise<MoexCandle[]> {
  const bcsInterval = BCS_INTERVAL_MAP[timeframe];
  if (!bcsInterval) return [];

  const safeWindowDays = BCS_SAFE_WINDOW_DAYS[timeframe] ?? 3;
  const tillMs  = endDateExclusive.getTime() - 1000; // 1 s before oldest known candle
  const fromMs  = tillMs - safeWindowDays * 86_400_000;

  const fromIso = new Date(fromMs).toISOString();
  const tillIso = new Date(tillMs).toISOString();

  const candles = await fetchBcsCandles(ticker, classCode, bcsInterval, fromIso, tillIso);
  candles.sort((a, b) => (a.begin < b.begin ? -1 : a.begin > b.begin ? 1 : 0));
  return candles;
}

// Recent-candle window for live polling (days to look back per timeframe).
const BCS_RECENT_WINDOW_DAYS: Record<string, number> = {
  '5m':  1,
  '15m': 2,
  '1h':  7,
  '4h':  30,
};

// Fetches the trailing window of candles for live polling.
// Returns [] for daily timeframes, unknown timeframes, or on any error.
export async function loadBcsRecentCandles(
  ticker: string,
  classCode: string,
  timeframe: string,
): Promise<MoexCandle[]> {
  if (timeframe === '1d') return [];

  const bcsInterval = BCS_INTERVAL_MAP[timeframe];
  if (!bcsInterval) return [];

  const windowDays = BCS_RECENT_WINDOW_DAYS[timeframe] ?? 1;
  const now = new Date();
  const fromMs = now.getTime() - windowDays * 86_400_000;

  const fromIso = new Date(fromMs).toISOString();
  const toIso   = now.toISOString();

  try {
    const candles = await fetchBcsCandles(ticker, classCode, bcsInterval, fromIso, toIso);
    candles.sort((a, b) => (a.begin < b.begin ? -1 : a.begin > b.begin ? 1 : 0));
    return candles;
  } catch {
    return [];
  }
}
