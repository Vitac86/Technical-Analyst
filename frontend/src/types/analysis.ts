export type SignalDirection = "buy" | "sell" | "neutral" | "caution" | "info";
export type SignalStrength = "weak" | "medium" | "strong" | "info";
export type AggregateSignal =
  | "strong_buy"
  | "buy"
  | "neutral"
  | "sell"
  | "strong_sell"
  | "caution";
export type Confidence = "low" | "medium" | "high";

export type TechnicalSignalItem = {
  indicator_name: string;
  label: string;
  value: Record<string, number> | number | string | null;
  signal: SignalDirection;
  score: number;
  strength: SignalStrength;
  reason: string;
  timestamp: string | null;
};

export type TechnicalSignalAggregate = {
  instrument_id: number;
  timeframe: string;
  total_score: number;
  signal: AggregateSignal;
  confidence: Confidence;
  bullish_count: number;
  bearish_count: number;
  caution_count: number;
  info_count: number;
  generated_at: string;
};

export type TechnicalSignalResponse = {
  instrument_id: number;
  timeframe: string;
  aggregate: TechnicalSignalAggregate;
  signals: TechnicalSignalItem[];
};
