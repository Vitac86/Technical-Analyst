import type { MoexCandle, MoexQuote, MoexSource } from '../api/moexDirect';

export type { MoexCandle, MoexQuote, MoexSource };

export type MarketDataProviderId = 'moex' | 'bcs';

export type Timeframe = '5m' | '15m' | '1h' | '4h' | '1d';

export type CandleLoadParams = {
  source: MoexSource;
  timeframe: Timeframe;
  from: string;
  till: string;
};

export type MarketDataProvider = {
  id: MarketDataProviderId;
  label: string;
  loadCandles(params: CandleLoadParams): Promise<MoexCandle[]>;
  loadRecentCandles(source: MoexSource, timeframe: Timeframe, signal?: AbortSignal): Promise<MoexCandle[]>;
  fetchQuote(source: MoexSource, signal?: AbortSignal): Promise<MoexQuote | null>;
};
