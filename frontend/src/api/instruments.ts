import { apiClient } from "./client";
import type { Instrument } from "../types/instrument";

export function getInstruments(): Promise<Instrument[]> {
  return apiClient.get<Instrument[]>("/instruments");
}

export const fetchInstruments = getInstruments;
