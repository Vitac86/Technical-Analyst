import { apiClient } from "./client";
import type { Instrument } from "../types/instrument";

export function fetchInstruments(): Promise<Instrument[]> {
  return apiClient.get<Instrument[]>("/instruments");
}
