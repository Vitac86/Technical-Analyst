import { searchMoex } from './moexDirect';
import type { MoexSearchResult } from './moexDirect';
import { loadBcsGoodsInstruments } from './bcsInstruments';
import type { BcsInstrument } from './bcsInstruments';
import { hasRefreshToken } from '../security/tokenStorage';

export type SourceProvider = 'moex' | 'bcs';
export type AssetGroup = 'goods' | 'stock' | 'fx' | 'unknown';

export type MobileAssetSearchResult = MoexSearchResult & {
  sourceProvider: SourceProvider;
  assetGroup?: AssetGroup;
  classCode?: string;
  displayName?: string;
  instrumentType?: string;
  tradingCurrency?: string;
};

export type MobileAssetSearchResponse = {
  results: MobileAssetSearchResult[];
  bcsTokenRequired: boolean;
  bcsUnavailable: boolean;
};

function normalizeText(value: string | undefined): string {
  return (value ?? '').trim().toLocaleLowerCase('ru-RU');
}

function assetGroupForMoex(result: MoexSearchResult): AssetGroup {
  if (result.market === 'selt') return 'fx';
  if (result.group.startsWith('stock_')) return 'stock';
  return 'unknown';
}

function toMoexResult(result: MoexSearchResult): MobileAssetSearchResult {
  return {
    ...result,
    sourceProvider: 'moex',
    assetGroup: assetGroupForMoex(result),
    classCode: result.board,
  };
}

function toBcsResult(instrument: BcsInstrument): MobileAssetSearchResult {
  return {
    ticker: instrument.ticker,
    name: instrument.displayName || instrument.name || instrument.shortname || instrument.ticker,
    group: `bcs_${instrument.assetGroup ?? 'unknown'}`,
    engine: 'bcs',
    market: instrument.assetGroup === 'goods' ? 'goods' : 'unknown',
    board: instrument.classCode,
    sourceProvider: 'bcs',
    assetGroup: instrument.assetGroup ?? 'unknown',
    classCode: instrument.classCode,
    displayName: instrument.displayName,
    instrumentType: instrument.instrumentType,
    tradingCurrency: instrument.tradingCurrency,
  };
}

function matchesBcsInstrument(instrument: BcsInstrument, query: string): boolean {
  const haystack = [
    instrument.ticker,
    instrument.secid,
    instrument.classCode,
    instrument.shortname,
    instrument.name,
    instrument.displayName,
    instrument.instrumentType,
    instrument.tradingCurrency,
    instrument.isin,
  ].map(normalizeText);

  return haystack.some(value => value.includes(query));
}

function scoreResult(result: MobileAssetSearchResult, query: string): number {
  const ticker = normalizeText(result.ticker);
  const name = normalizeText(result.name);
  const board = normalizeText(result.board);
  const bcsBoost = result.sourceProvider === 'bcs' ? -4 : 0;

  if (ticker === query) return bcsBoost;
  if (ticker.startsWith(query)) return 20 + bcsBoost;
  if (board === query) return 35 + bcsBoost;
  if (name.startsWith(query)) return 40 + bcsBoost;
  if (ticker.includes(query)) return 55 + bcsBoost;
  if (name.includes(query)) return 70 + bcsBoost;
  return 100 + bcsBoost;
}

function dedupeByTickerPreferBcs(results: MobileAssetSearchResult[]): MobileAssetSearchResult[] {
  const selected = new Map<string, MobileAssetSearchResult>();

  for (const result of results) {
    const key = result.ticker.toUpperCase();
    const existing = selected.get(key);
    if (!existing) {
      selected.set(key, result);
      continue;
    }

    if (existing.sourceProvider !== 'bcs' && result.sourceProvider === 'bcs') {
      selected.set(key, result);
    }
  }

  return Array.from(selected.values());
}

export async function searchMobileAssets(query: string): Promise<MobileAssetSearchResponse> {
  const trimmed = query.trim();
  const normalizedQuery = normalizeText(trimmed);
  if (normalizedQuery.length < 2) {
    return { results: [], bcsTokenRequired: false, bcsUnavailable: false };
  }

  const bcsTokenAvailable = hasRefreshToken();
  let bcsUnavailable = false;

  const moexPromise = searchMoex(trimmed)
    .then(results => results.map(toMoexResult))
    .catch(() => []);

  const bcsPromise = bcsTokenAvailable
    ? loadBcsGoodsInstruments()
        .then(instruments => instruments
          .filter(instrument => matchesBcsInstrument(instrument, normalizedQuery))
          .map(toBcsResult))
        .catch(() => {
          bcsUnavailable = true;
          return [];
        })
    : Promise.resolve([]);

  const [moexResults, bcsResults] = await Promise.all([moexPromise, bcsPromise]);

  const results = dedupeByTickerPreferBcs([...bcsResults, ...moexResults])
    .sort((a, b) => {
      const scoreDelta = scoreResult(a, normalizedQuery) - scoreResult(b, normalizedQuery);
      if (scoreDelta !== 0) return scoreDelta;
      return a.ticker.localeCompare(b.ticker);
    });

  return {
    results,
    bcsTokenRequired: !bcsTokenAvailable,
    bcsUnavailable,
  };
}
