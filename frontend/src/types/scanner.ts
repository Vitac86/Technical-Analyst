export type ScanStatus = "ok" | "no_instrument" | "no_candles" | "no_indicators" | "error";

export interface ScannerInstrumentRequest {
  ticker: string;
  engine?: string;
  market?: string;
  board?: string;
}

export interface ScannerRequest {
  instruments: ScannerInstrumentRequest[];
  timeframe: string;
  lookback?: number;
}

export interface ScannerRow {
  ticker: string;
  name: string | null;
  engine: string | null;
  market: string | null;
  board: string | null;
  instrument_id: number | null;
  timeframe: string;
  status: ScanStatus;
  last_close: number | null;
  change_percent: number | null;
  aggregate_signal: string | null;
  total_score: number | null;
  confidence: string | null;
  bullish_count: number | null;
  bearish_count: number | null;
  caution_count: number | null;
  rsi: number | null;
  macd_histogram: number | null;
  atr_percent: number | null;
  nearest_support: number | null;
  nearest_resistance: number | null;
  distance_to_support_percent: number | null;
  distance_to_resistance_percent: number | null;
  summary: string | null;
  error: string | null;
}

export interface ScannerResponse {
  timeframe: string;
  rows: ScannerRow[];
  generated_at: string;
}
