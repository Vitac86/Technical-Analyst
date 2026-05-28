import { loadMoexCandles, loadMoexRecentCandles, fetchMoexQuote } from '../api/moexDirect';
import type { MarketDataProvider, CandleLoadParams, MoexCandle, MoexQuote, MoexSource, Timeframe } from './types';

export const moexProvider: MarketDataProvider = {
  id: 'moex',
  label: 'MOEX',

  loadCandles({ source, timeframe, from, till }: CandleLoadParams): Promise<MoexCandle[]> {
    return loadMoexCandles(source, timeframe, from, till);
  },

  loadRecentCandles(source: MoexSource, timeframe: Timeframe, signal?: AbortSignal): Promise<MoexCandle[]> {
    return loadMoexRecentCandles(source, timeframe, signal);
  },

  fetchQuote(source: MoexSource, signal?: AbortSignal): Promise<MoexQuote | null> {
    return fetchMoexQuote(source, signal);
  },
};
