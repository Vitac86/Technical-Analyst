export type Candle = {
  id: number;
  instrument_id: number;
  timeframe: string;
  timestamp: string;
  open: number | string;
  high: number | string;
  low: number | string;
  close: number | string;
  volume: number | string | null;
};
