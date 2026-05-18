import type { Instrument } from "./instrument";

export type LastPriceSummary = {
  last_close: number | null;
  previous_close: number | null;
  change: number | null;
  change_percent: number | null;
  last_timestamp: string | null;
};

export type WorkspaceLoadRequest = {
  ticker: string;
  engine: string;
  market: string;
  board: string;
  timeframe: string;
  start: string;
  end: string;
  calculate_indicators: boolean;
};

export type WorkspaceLoadResponse = {
  instrument: Instrument;
  candle_sync: Record<string, unknown>;
  indicator_sync: Record<string, unknown> | null;
  last_price: LastPriceSummary;
};
