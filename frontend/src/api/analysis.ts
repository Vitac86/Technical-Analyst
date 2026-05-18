import { apiClient } from "./client";
import type { AnalysisSignal } from "../types/signal";

export function fetchSignals(): Promise<AnalysisSignal[]> {
  return apiClient.get<AnalysisSignal[]>("/analysis/signals");
}
