import { getBcsAccessToken } from './bcsAuth';
import { hasRefreshToken } from '../security/tokenStorage';

const BCS_INSTRUMENTS_BY_TYPE_URL =
  'https://be.broker.ru/trade-api-information-service/api/v1/instruments/by-type';

const PAGE_SIZE = 50;
const MAX_PAGES = 20;

export type BcsInstrumentType = 'GOODS' | string;

export type BcsInstrumentBoard = {
  classCode: string;
  exchange: string;
};

export type BcsInstrument = {
  source: 'bcs';
  secid: string;
  ticker: string;
  boardid: string;
  classCode: string;
  primaryBoard: string;
  boards: BcsInstrumentBoard[];
  shortname: string;
  name: string;
  displayName: string;
  instrumentType: string;
  tradingCurrency?: string;
  isin?: string;
  lotSize?: number;
  minimumStep?: number;
  assetGroup?: 'goods' | 'stock' | 'fx' | 'unknown';
};

export type BcsInstrumentsLoadStatus = 'live' | 'merged' | 'fallback' | 'fallback_error';

export type BcsInstrumentsLoadResult = {
  instruments: BcsInstrument[];
  status: BcsInstrumentsLoadStatus;
};

// Last status seen for diagnostics in the UI. Updated only by loadBcsGoodsInstruments.
let _lastGoodsStatus: BcsInstrumentsLoadStatus = 'fallback';

export function getLastGoodsLoadStatus(): BcsInstrumentsLoadStatus {
  return _lastGoodsStatus;
}

// ---------------------------------------------------------------------------
// Static fallback list — confirmed offline via ml/data/instruments/bcs_GOODS.json
// Contains instrument metadata only. No tokens, candles, orderbook, or prices.
// Safe to commit.
// ---------------------------------------------------------------------------

function buildStaticGoods(
  ticker: string,
  classCode: string,
  displayName: string,
): BcsInstrument {
  return {
    source: 'bcs',
    secid: ticker,
    ticker,
    boardid: classCode,
    classCode,
    primaryBoard: classCode,
    boards: [{ classCode, exchange: 'MOEX' }],
    shortname: displayName,
    name: displayName,
    displayName,
    instrumentType: 'GOODS',
    assetGroup: 'goods',
  };
}

export const STATIC_BCS_GOODS_INSTRUMENTS: BcsInstrument[] = [
  buildStaticGoods('AL3M',         'FEM', 'Алюминий'),
  buildStaticGoods('BRENT0826',    'FEG', 'Нефть BRENT'),
  buildStaticGoods('COPPER3M',     'FEM', 'Медь'),
  buildStaticGoods('ETH',          'FEV', 'ETH'),
  buildStaticGoods('GOLD',         'FEG', 'Золото'),
  buildStaticGoods('LCROIL0726NY', 'FEG', 'Нефть WTI'),
  buildStaticGoods('NGAS0726',     'FEG', 'Природный газ'),
  buildStaticGoods('NICKEL3M',     'FEM', 'Никель'),
  buildStaticGoods('PALLAD',       'FEG', 'Палладий'),
  buildStaticGoods('PLATINUM',     'FEG', 'Платина'),
  buildStaticGoods('SILVER',       'FEG', 'Серебро'),
  buildStaticGoods('XBT',          'FEV', 'XBT'),
  buildStaticGoods('ZINC3M',       'FEM', 'Цинк'),
];

const cache = new Map<string, BcsInstrument[]>();
const inFlight = new Map<string, Promise<BcsInstrument[]>>();

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function readValue(row: Record<string, unknown>, keys: string[]): unknown {
  for (const key of keys) {
    if (key in row) return row[key];

    const lower = key.toLowerCase();
    const upper = key.toUpperCase();
    if (lower in row) return row[lower];
    if (upper in row) return row[upper];
  }
  return undefined;
}

function readString(row: Record<string, unknown>, keys: string[]): string | undefined {
  const value = readValue(row, keys);
  if (typeof value === 'string') {
    const trimmed = value.trim();
    return trimmed.length > 0 ? trimmed : undefined;
  }
  if (typeof value === 'number' && Number.isFinite(value)) {
    return String(value);
  }
  return undefined;
}

function readNumber(row: Record<string, unknown>, keys: string[]): number | undefined {
  const value = readValue(row, keys);
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string') {
    const n = Number(value.replace(',', '.'));
    if (Number.isFinite(n)) return n;
  }
  return undefined;
}

function readBoards(row: Record<string, unknown>): BcsInstrumentBoard[] {
  const raw = readValue(row, ['boards', 'boardList']);
  if (!Array.isArray(raw)) return [];

  const result: BcsInstrumentBoard[] = [];
  for (const item of raw) {
    const r = asRecord(item);
    if (!r) continue;
    const classCode = readString(r, ['classCode', 'class_code', 'board', 'boardId']);
    const exchange  = readString(r, ['exchange', 'exchangeCode']) ?? 'MOEX';
    if (classCode) result.push({ classCode, exchange });
  }
  return result;
}

function assetGroupForType(type: BcsInstrumentType, row: Record<string, unknown>): BcsInstrument['assetGroup'] {
  const rawGroup = readString(row, ['assetGroup', 'asset_group', 'group']);
  const normalizedGroup = rawGroup?.toLowerCase();
  if (normalizedGroup === 'goods' || normalizedGroup === 'commodity' || normalizedGroup === 'commodities') {
    return 'goods';
  }

  const normalizedType = type.toUpperCase();
  if (normalizedType === 'GOODS') return 'goods';
  return 'unknown';
}

function normalizeInstrument(
  raw: unknown,
  type: BcsInstrumentType,
): BcsInstrument | null {
  const row = asRecord(raw);
  if (!row) return null;

  const ticker = readString(row, [
    'ticker',
    'secid',
    'secId',
    'securityCode',
    'symbol',
    'code',
  ]);
  const primaryBoard = readString(row, ['primaryBoard', 'primary_board']);
  const classCode = readString(row, [
    'classCode',
    'class_code',
    'boardid',
    'boardId',
    'board',
    'marketCode',
  ]) ?? primaryBoard;

  if (!ticker || !classCode) return null;

  const shortname =
    readString(row, ['shortName', 'shortname', 'short_name', 'shortTitle', 'name']) ?? ticker;
  const displayName =
    readString(row, ['displayName', 'display_name', 'fullName', 'full_name', 'name']) ?? shortname;
  const instrumentType =
    readString(row, ['instrumentType', 'instrument_type', 'type', 'kind']) ?? type;

  const boards = readBoards(row);
  const finalBoards = boards.length > 0 ? boards : [{ classCode, exchange: 'MOEX' }];

  return {
    source: 'bcs',
    secid: ticker,
    ticker,
    boardid: classCode,
    classCode,
    primaryBoard: primaryBoard ?? classCode,
    boards: finalBoards,
    shortname,
    name: displayName || shortname,
    displayName: displayName || shortname,
    instrumentType,
    assetGroup: assetGroupForType(type, row),
    tradingCurrency: readString(row, ['tradingCurrency', 'currency', 'currencyCode']),
    isin: readString(row, ['isin', 'ISIN']),
    lotSize: readNumber(row, ['lotSize', 'lot', 'lot_size']),
    minimumStep: readNumber(row, ['minimumStep', 'minStep', 'priceStep', 'step']),
  };
}

function extractRawItems(json: unknown): { known: boolean; items: unknown[] } {
  if (Array.isArray(json)) return { known: true, items: json };

  const root = asRecord(json);
  if (!root) return { known: false, items: [] };

  const directKeys = ['items', 'data', 'content', 'result'];
  for (const key of directKeys) {
    const value = root[key];
    if (Array.isArray(value)) return { known: true, items: value };
  }

  const data = asRecord(root.data);
  if (data && Array.isArray(data.items)) {
    return { known: true, items: data.items };
  }

  return { known: false, items: [] };
}

function warnUnknownShape(json: unknown): void {
  const keys = asRecord(json) ? Object.keys(json as Record<string, unknown>) : [];
  console.warn(
    `BCS instruments response shape not recognised. Top-level keys: [${keys.join(', ')}]`,
  );
}

async function fetchBcsInstrumentsByType(type: BcsInstrumentType): Promise<BcsInstrument[]> {
  const token = await getBcsAccessToken();
  const all: BcsInstrument[] = [];

  for (let page = 0; page < MAX_PAGES; page += 1) {
    const url = new URL(BCS_INSTRUMENTS_BY_TYPE_URL);
    url.searchParams.set('type', type);
    url.searchParams.set('size', String(PAGE_SIZE));
    url.searchParams.set('page', String(page));

    const resp = await fetch(url.toString(), {
      method: 'GET',
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: 'application/json',
      },
    });

    if (!resp.ok) {
      throw new Error(`BCS instruments failed: HTTP ${resp.status}`);
    }

    let json: unknown;
    try {
      json = await resp.json();
    } catch {
      console.warn('BCS instruments response was not valid JSON.');
      return [];
    }

    const extracted = extractRawItems(json);
    if (!extracted.known) {
      warnUnknownShape(json);
      return [];
    }

    const pageItems = extracted.items
      .map(item => normalizeInstrument(item, type))
      .filter((item): item is BcsInstrument => item !== null);

    all.push(...pageItems);

    if (extracted.items.length === 0 || extracted.items.length < PAGE_SIZE) {
      break;
    }
  }

  return all;
}

export function loadBcsInstrumentsByType(type: BcsInstrumentType): Promise<BcsInstrument[]> {
  const key = type.toUpperCase();
  const cached = cache.get(key);
  if (cached) return Promise.resolve(cached);

  const existing = inFlight.get(key);
  if (existing) return existing;

  const request = fetchBcsInstrumentsByType(key)
    .then(items => {
      cache.set(key, items);
      inFlight.delete(key);
      return items;
    })
    .catch(err => {
      inFlight.delete(key);
      throw err;
    });

  inFlight.set(key, request);
  return request;
}

// ---------------------------------------------------------------------------
// GOODS loader with static fallback
//
// Behaviour:
//   - No token         -> static fallback only (status: 'fallback').
//   - Live ok, count>0 -> merge live + static, live wins on (ticker, classCode)
//                         (status: 'merged' or 'live' if no extra static added).
//   - Live ok, count=0 -> static fallback (status: 'fallback').
//   - Live throws      -> static fallback (status: 'fallback_error').
//
// Never throws — search must always be able to surface confirmed GOODS items.
// ---------------------------------------------------------------------------

function dedupeMerge(
  live: BcsInstrument[],
  fallback: BcsInstrument[],
): { merged: BcsInstrument[]; addedFromFallback: number } {
  const seen = new Map<string, BcsInstrument>();
  for (const item of live) {
    seen.set(`${item.ticker}:${item.classCode}`, item);
  }
  let added = 0;
  for (const item of fallback) {
    const key = `${item.ticker}:${item.classCode}`;
    if (!seen.has(key)) {
      seen.set(key, item);
      added += 1;
    }
  }
  return { merged: Array.from(seen.values()), addedFromFallback: added };
}

export async function loadBcsGoodsInstruments(): Promise<BcsInstrument[]> {
  if (!hasRefreshToken()) {
    _lastGoodsStatus = 'fallback';
    return STATIC_BCS_GOODS_INSTRUMENTS.slice();
  }

  try {
    const live = await loadBcsInstrumentsByType('GOODS');
    if (live.length === 0) {
      _lastGoodsStatus = 'fallback';
      return STATIC_BCS_GOODS_INSTRUMENTS.slice();
    }
    const { merged, addedFromFallback } = dedupeMerge(live, STATIC_BCS_GOODS_INSTRUMENTS);
    _lastGoodsStatus = addedFromFallback > 0 ? 'merged' : 'live';
    return merged;
  } catch {
    _lastGoodsStatus = 'fallback_error';
    return STATIC_BCS_GOODS_INSTRUMENTS.slice();
  }
}

// Diagnostic variant — surfaces both the result and the load status so
// callers can show a "fallback list used" hint without an extra round-trip.
export async function loadBcsGoodsInstrumentsWithStatus(): Promise<BcsInstrumentsLoadResult> {
  const instruments = await loadBcsGoodsInstruments();
  return { instruments, status: _lastGoodsStatus };
}
