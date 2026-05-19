import { apiClient } from "./client";
import type { AnalysisSignal } from "../types/signal";
import type { TechnicalSignalResponse } from "../types/analysis";
import type { TechnicalLevelsResponse } from "../types/levels";

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

export function getTechnicalLevels(
  instrumentId: number,
  timeframe: string,
  lookback = 100,
): Promise<TechnicalLevelsResponse> {
  return apiClient.get<TechnicalLevelsResponse>(
    `/analysis/levels?instrument_id=${instrumentId}&timeframe=${timeframe}&lookback=${lookback}`,
  );
}
