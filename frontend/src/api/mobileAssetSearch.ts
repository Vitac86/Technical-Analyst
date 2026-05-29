import { searchMoex } from './moexDirect';
import type { MoexSearchResult } from './moexDirect';
import {
  loadBcsGoodsInstrumentsWithStatus,
  type BcsInstrument,
  type BcsInstrumentsLoadStatus,
} from './bcsInstruments';
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
  bcsResults: MobileAssetSearchResult[];
  moexResults: MobileAssetSearchResult[];
  bcsTokenRequired: boolean;
  bcsFallbackUsed: boolean;
  bcsLoadStatus: BcsInstrumentsLoadStatus;
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

// Lower score = better match. BCS GOODS gets a boost so confirmed commodity
// instruments rise above the MOEX noise (e.g. for "GOLD" or "золото").
function scoreResult(result: MobileAssetSearchResult, query: string): number {
  const ticker = normalizeText(result.ticker);
  const name = normalizeText(result.name);
  const board = normalizeText(result.board);
  const bcsBoost = result.sourceProvider === 'bcs' ? -10 : 0;

  if (ticker === query) return 0 + bcsBoost;
  if (ticker.startsWith(query)) return 20 + bcsBoost;
  if (board === query) return 35 + bcsBoost;
  if (name === query) return 25 + bcsBoost;
  if (name.startsWith(query)) return 40 + bcsBoost;
  if (ticker.includes(query)) return 55 + bcsBoost;
  if (name.includes(query)) return 70 + bcsBoost;
  return 100 + bcsBoost;
}

// Dedupe key includes the source provider, engine, market, and class/board —
// MOEX GOLD (TQTF ETF) and BCS GOLD (FEG commodity) are different instruments
// and must NOT collapse into a single row.
function dedupeKey(r: MobileAssetSearchResult): string {
  if (r.sourceProvider === 'bcs') {
    return `bcs:${r.assetGroup ?? 'unknown'}:${r.ticker.toUpperCase()}:${(r.classCode ?? r.board).toUpperCase()}`;
  }
  return `moex:${r.engine}:${r.market}:${r.board}:${r.ticker.toUpperCase()}`;
}

function dedupe(results: MobileAssetSearchResult[]): MobileAssetSearchResult[] {
  const seen = new Map<string, MobileAssetSearchResult>();
  for (const result of results) {
    const key = dedupeKey(result);
    if (!seen.has(key)) seen.set(key, result);
  }
  return Array.from(seen.values());
}

function sortByScore(
  results: MobileAssetSearchResult[],
  query: string,
): MobileAssetSearchResult[] {
  return results.slice().sort((a, b) => {
    const scoreDelta = scoreResult(a, query) - scoreResult(b, query);
    if (scoreDelta !== 0) return scoreDelta;
    return a.ticker.localeCompare(b.ticker);
  });
}

export async function searchMobileAssets(query: string): Promise<MobileAssetSearchResponse> {
  const trimmed = query.trim();
  const normalizedQuery = normalizeText(trimmed);
  if (normalizedQuery.length < 2) {
    return {
      results: [],
      bcsResults: [],
      moexResults: [],
      bcsTokenRequired: false,
      bcsFallbackUsed: false,
      bcsLoadStatus: 'fallback',
    };
  }

  const bcsTokenAvailable = hasRefreshToken();

  const moexPromise = searchMoex(trimmed)
    .then(results => results.map(toMoexResult))
    .catch(() => [] as MobileAssetSearchResult[]);

  // BCS GOODS search always runs — loadBcsGoodsInstrumentsWithStatus never throws
  // and returns the static fallback when the live endpoint is unavailable or no
  // token is configured. This lets users find GOLD/BRENT/etc. even before they
  // paste a BCS token; the chart will then prompt for one when opened.
  const bcsPromise = loadBcsGoodsInstrumentsWithStatus()
    .then(({ instruments, status }) => ({
      results: instruments
        .filter(instrument => matchesBcsInstrument(instrument, normalizedQuery))
        .map(toBcsResult),
      status,
    }))
    .catch(() => ({ results: [] as MobileAssetSearchResult[], status: 'fallback_error' as const }));

  const [moexResults, bcs] = await Promise.all([moexPromise, bcsPromise]);

  const bcsResults = sortByScore(dedupe(bcs.results), normalizedQuery);
  const moexResultsSorted = sortByScore(dedupe(moexResults), normalizedQuery);

  // Combined ranking still uses score-based sort so that exact-name BCS matches
  // (e.g. "GOLD" -> ticker GOLD, "золото" -> displayName "Золото") are ranked
  // first even when AssetDrawer renders results as a single list.
  const combined = sortByScore(dedupe([...bcs.results, ...moexResults]), normalizedQuery);

  return {
    results: combined,
    bcsResults,
    moexResults: moexResultsSorted,
    bcsTokenRequired: !bcsTokenAvailable && bcsResults.length > 0,
    bcsFallbackUsed: bcs.status === 'fallback' || bcs.status === 'fallback_error',
    bcsLoadStatus: bcs.status,
  };
}
