export type LevelKind =
  | "support"
  | "resistance"
  | "target_up"
  | "target_down"
  | "stop_zone"
  | "info";

export type TechnicalLevel = {
  kind: LevelKind;
  label: string;
  price: number | null;
  distance_percent: number | null;
  reason: string;
};

export type TechnicalLevelsResponse = {
  instrument_id: number;
  timeframe: string;
  last_close: number | null;
  atr: number | null;
  atr_percent: number | null;
  lookback: number;
  levels: TechnicalLevel[];
  summary: string;
  generated_at: string;
  message?: string | null;
};
