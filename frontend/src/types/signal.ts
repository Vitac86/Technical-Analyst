export type AnalysisSignal = {
  id: number;
  instrument_id: number;
  timeframe: string;
  signal_type: string;
  direction: string;
  strength: string | null;
  generated_at: string;
  expires_at: string | null;
  payload: Record<string, unknown> | null;
};
