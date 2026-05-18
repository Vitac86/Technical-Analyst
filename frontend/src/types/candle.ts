export type Candle = {
  id: number;
  instrument_id: number;
  timeframe: string;
  timestamp: string;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string | null;
};
