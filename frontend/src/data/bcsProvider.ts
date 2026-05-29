import { loadBcsCandles, loadBcsRecentCandles } from '../api/bcsMarketData';
import type { MarketDataProvider, CandleLoadParams, MoexCandle, MoexQuote, MoexSource } from './types';

// BCS live polling is not yet implemented; loadRecentCandles returns []
// so the caller can disable live mode or fall back to MOEX quotes.
// BCS quote endpoint is not yet mapped; fetchQuote returns null,
// which causes AssetDrawer to continue using MOEX quotes automatically.

export const bcsProvider: MarketDataProvider = {
  id: 'bcs',
  label: 'BCS',

  loadCandles({ source, timeframe, from, till }: CandleLoadParams): Promise<MoexCandle[]> {
    const classCode = source.classCode || source.board || 'TQBR';
    return loadBcsCandles(source.ticker, classCode, timeframe, from, till);
  },

  async loadRecentCandles(source: MoexSource, timeframe: string): Promise<MoexCandle[]> {
    const classCode = source.classCode || source.board || 'TQBR';
    return loadBcsRecentCandles(source.ticker, classCode, timeframe);
  },

  async fetchQuote(_source: MoexSource, _signal?: AbortSignal): Promise<MoexQuote | null> {
    return null;
  },
};
