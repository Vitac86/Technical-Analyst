export type IndicatorPayloadValue = number | string | null;

export type IndicatorValue = {
  id: number;
  instrument_id: number;
  indicator_name: string;
  category: string;
  timeframe: string;
  timestamp: string;
  values: Record<string, IndicatorPayloadValue>;
};
