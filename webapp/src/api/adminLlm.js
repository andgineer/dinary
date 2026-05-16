import { apiRequest } from "./_request.js";

export const createProvider = (data) =>
  apiRequest("/api/admin/llm-providers", { method: "POST", body: data });
export const updateProvider = (id, patch) =>
  apiRequest(`/api/admin/llm-providers/${id}`, { method: "PATCH", body: patch });
export const deleteProvider = (id) =>
  apiRequest(`/api/admin/llm-providers/${id}`, { method: "DELETE" });
export const testProvider = (id) =>
  apiRequest(`/api/admin/llm-providers/${id}/test`, { method: "POST" });
export const getStatus = () => apiRequest("/api/admin/llm-status");
