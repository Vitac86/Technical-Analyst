import { apiClient } from "./client";
import type { AnalysisSignal } from "../types/signal";
import type { TechnicalSignalResponse } from "../types/analysis";

export function fetchSignals(): Promise<AnalysisSignal[]> {
  return apiClient.get<AnalysisSignal[]>("/analysis/signals");
}

export function getTechnicalSignals(
  instrumentId: number,
  timeframe: string,
): Promise<TechnicalSignalResponse> {
  return apiClient.get<TechnicalSignalResponse>(
    `/analysis/technical-signals?instrument_id=${instrumentId}&timeframe=${timeframe}`,
  );
}
