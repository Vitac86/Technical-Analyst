import { apiClient } from "./client";
import type { WorkspaceLoadRequest, WorkspaceLoadResponse } from "../types/workspace";

export function loadWorkspace(
  request: WorkspaceLoadRequest,
): Promise<WorkspaceLoadResponse> {
  return apiClient.post<WorkspaceLoadResponse>("/workspace", request);
}
