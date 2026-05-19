import { apiClient } from "./client";
import type { ScannerRequest, ScannerResponse } from "../types/scanner";

export function runScanner(request: ScannerRequest): Promise<ScannerResponse> {
  return apiClient.post<ScannerResponse>("/analysis/scanner", request);
}
