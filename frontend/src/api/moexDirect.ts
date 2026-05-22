// Direct MOEX ISS data client.
// No caching, no local persistence for candles.
// Candles live only in React state; fetched fresh on every user action.

const MOEX_ISS = 'https://iss.moex.com/iss';

// Maximum pages to fetch per candle load (~500 rows per page = up to 6000 raw candles).
const MAX_PAGES = 12;

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

export type MoexSource = {
  ticker: string;
  engine: string;
  market: string;
  board: string;
};

export type MoexSearchResult = {
  ticker: string;
  name: string;
  group: string;
  engine: string;
  market: string;
  board: string;
};

// Candle as returned by MOEX (begin is "YYYY-MM-DD HH:MM:SS", Moscow time).
export type MoexCandle = {
  begin: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

// ---------------------------------------------------------------------------
// Timeframe → MOEX interval + optional aggregation
// ---------------------------------------------------------------------------

type TfConfig = {
  interval: number;
  bucketMinutes: number | null; // null = no client-side aggregation needed
};

export const TF_CONFIG: Record<string, TfConfig> = {
  '5m':  { interval: 1,  bucketMinutes: 5 },
  '15m': { interval: 1,  bucketMinutes: 15 },
  '1h':  { interval: 60, bucketMinutes: null },
  '4h':  { interval: 60, bucketMinutes: 240 },
  '1d':  { interval: 24, bucketMinutes: null },
};

// ---------------------------------------------------------------------------
// Group → source mapping
// ---------------------------------------------------------------------------

type GroupInfo = { engine: string; market: string; board: string };

const GROUP_MAP: Record<string, GroupInfo> = {
  stock_shares:  { engine: 'stock',    market: 'shares', board: 'TQBR' },
  stock_bonds:   { engine: 'stock',    market: 'bonds',  board: 'TQCB' },
  stock_etf:     { engine: 'stock',    market: 'shares', board: 'TQTF' },
  stock_ppif:    { engine: 'stock',    market: 'paif',   board: 'TQIF' },
  futures_forts: { engine: 'futures',  market: 'forts',  board: 'RFUD' },
  currency_selt: { engine: 'currency', market: 'selt',   board: 'CETS' },
};

// ---------------------------------------------------------------------------
// MOEX table response parser
// ---------------------------------------------------------------------------

function parseTable<T>(raw: { columns: string[]; data: unknown[][] }): T[] {
  const { columns, data } = raw;
  return data.map(row => {
    const obj: Record<string, unknown> = {};
    columns.forEach((col, i) => { obj[col] = row[i]; });
    return obj as unknown as T;
  });
}

// ---------------------------------------------------------------------------
// Instrument search
// ---------------------------------------------------------------------------

type SecRow = {
  secid?: string;
  name?: string;
  shortname?: string;
  emitent_title?: string;
  type?: string;
  group?: string;
  primary_boardid?: string;
};

export async function searchMoex(query: string): Promise<MoexSearchResult[]> {
  const url = new URL(`${MOEX_ISS}/securities.json`);
  url.searchParams.set('q', query);
  url.searchParams.set('limit', '20');
  url.searchParams.set('is_trading', '1');
  url.searchParams.set('iss.meta', 'off');
  url.searchParams.set('lang', 'ru');

  const resp = await fetch(url.toString());
  if (!resp.ok) throw new Error(`MOEX search failed: HTTP ${resp.status}`);

  const json = await resp.json() as {
    securities: { columns: string[]; data: unknown[][] };
  };

  const rows = parseTable<SecRow>(json.securities);
  const results: MoexSearchResult[] = [];

  for (const r of rows) {
    if (!r.secid) continue;

    const group = r.group ?? '';
    const info = GROUP_MAP[group];
    const board = r.primary_boardid || info?.board || 'TQBR';

    // Skip instruments where we can't determine any board
    if (!r.primary_boardid && !info) continue;

    results.push({
      ticker: r.secid,
      name: r.shortname || r.name || r.emitent_title || r.secid,
      group,
      engine: info?.engine ?? 'stock',
      market: info?.market ?? 'shares',
      board,
    });
  }

  return results;
}

// ---------------------------------------------------------------------------
// Candle loading with pagination
// ---------------------------------------------------------------------------

type RawCandleRow = {
  begin?: string;
  open?: number;
  close?: number;
  high?: number;
  low?: number;
  value?: number;
  volume?: number;
};

async function fetchCandlePage(
  src: MoexSource,
  from: string,
  till: string,
  interval: number,
  start: number,
): Promise<RawCandleRow[]> {
  const url = new URL(
    `${MOEX_ISS}/engines/${src.engine}/markets/${src.market}/boards/${src.board}/securities/${src.ticker}/candles.json`,
  );
  url.searchParams.set('from', from);
  url.searchParams.set('till', till);
  url.searchParams.set('interval', String(interval));
  url.searchParams.set('start', String(start));
  url.searchParams.set('iss.meta', 'off');

  const resp = await fetch(url.toString());
  if (!resp.ok) throw new Error(`MOEX candles failed: HTTP ${resp.status}`);

  const json = await resp.json() as {
    candles: { columns: string[]; data: unknown[][] };
  };

  return parseTable<RawCandleRow>(json.candles);
}

export async function loadMoexCandles(
  src: MoexSource,
  timeframe: string,
  from: string,
  till: string,
): Promise<MoexCandle[]> {
  const cfg = TF_CONFIG[timeframe];
  if (!cfg) throw new Error(`Unknown timeframe: ${timeframe}`);

  const allRaw: RawCandleRow[] = [];
  let offset = 0;

  for (let page = 0; page < MAX_PAGES; page++) {
    const rows = await fetchCandlePage(src, from, till, cfg.interval, offset);
    if (rows.length === 0) break;
    allRaw.push(...rows);
    offset += rows.length;
    if (rows.length < 500) break; // last page
  }

  // Deduplicate by begin timestamp and sort ascending
  const seen = new Set<string>();
  const unique = allRaw.filter(r => {
    const key = r.begin ?? '';
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  unique.sort((a, b) => {
    const ak = a.begin ?? '';
    const bk = b.begin ?? '';
    return ak < bk ? -1 : ak > bk ? 1 : 0;
  });

  const candles: MoexCandle[] = unique
    .filter((r): r is Required<Pick<RawCandleRow, 'begin' | 'open' | 'high' | 'low' | 'close'>> & RawCandleRow =>
      r.begin != null && r.open != null && r.high != null && r.low != null && r.close != null,
    )
    .map(r => ({
      begin: r.begin,
      open: r.open,
      high: r.high,
      low: r.low,
      close: r.close,
      // Prefer lot volume; fall back to ruble value for instruments without lot volume
      volume: (r.volume != null && r.volume > 0) ? r.volume : (r.value ?? 0),
    }));

  return cfg.bucketMinutes !== null
    ? aggregateCandles(candles, cfg.bucketMinutes)
    : candles;
}

// ---------------------------------------------------------------------------
// Client-side OHLCV aggregation (for 5m, 15m, 4h)
//
// MOEX timestamps are Moscow local time ("YYYY-MM-DD HH:MM:SS").
// We treat them as UTC when bucketing (common convention for MOEX apps)
// so chart labels show Moscow time — which is what Russian users expect.
// ---------------------------------------------------------------------------

function beginToFakeUtcSeconds(begin: string): number {
  // Append Z to treat Moscow time as if it were UTC
  return Math.floor(new Date(begin.replace(' ', 'T') + 'Z').getTime() / 1000);
}

function epochSecondsToMoexString(epochSec: number): string {
  const d = new Date(epochSec * 1000);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} `
    + `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}`;
}

export function aggregateCandles(candles: MoexCandle[], bucketMinutes: number): MoexCandle[] {
  if (candles.length === 0) return [];

  const bucketSec = bucketMinutes * 60;

  // Map: bucketTimestamp → accumulated candle
  const buckets = new Map<number, { candle: MoexCandle; bt: number }>();

  for (const c of candles) {
    const ts = beginToFakeUtcSeconds(c.begin);
    const bt = Math.floor(ts / bucketSec) * bucketSec;

    const entry = buckets.get(bt);
    if (!entry) {
      buckets.set(bt, {
        candle: {
          begin: epochSecondsToMoexString(bt),
          open: c.open,
          high: c.high,
          low: c.low,
          close: c.close,
          volume: c.volume,
        },
        bt,
      });
    } else {
      const b = entry.candle;
      b.high = Math.max(b.high, c.high);
      b.low = Math.min(b.low, c.low);
      b.close = c.close;
      b.volume += c.volume;
    }
  }

  return [...buckets.values()]
    .sort((a, b) => a.bt - b.bt)
    .map(e => e.candle);
}
