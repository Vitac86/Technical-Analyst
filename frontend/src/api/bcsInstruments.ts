import { getBcsAccessToken } from './bcsAuth';

const BCS_INSTRUMENTS_BY_TYPE_URL =
  'https://be.broker.ru/trade-api-information-service/api/v1/instruments/by-type';

const PAGE_SIZE = 50;
const MAX_PAGES = 20;

export type BcsInstrumentType = 'GOODS' | string;

export type BcsInstrument = {
  source: 'bcs';
  secid: string;
  ticker: string;
  boardid: string;
  classCode: string;
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
  const classCode = readString(row, [
    'classCode',
    'class_code',
    'boardid',
    'boardId',
    'board',
    'marketCode',
  ]);

  if (!ticker || !classCode) return null;

  const shortname =
    readString(row, ['shortName', 'shortname', 'short_name', 'shortTitle', 'name']) ?? ticker;
  const displayName =
    readString(row, ['displayName', 'display_name', 'fullName', 'full_name', 'name']) ?? shortname;
  const instrumentType =
    readString(row, ['instrumentType', 'instrument_type', 'type', 'kind']) ?? type;

  return {
    source: 'bcs',
    secid: ticker,
    ticker,
    boardid: classCode,
    classCode,
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

export function loadBcsGoodsInstruments(): Promise<BcsInstrument[]> {
  return loadBcsInstrumentsByType('GOODS');
}
