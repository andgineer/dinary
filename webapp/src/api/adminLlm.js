import { apiRequest } from "./_request.js";

export const createProvider = (data) =>
  apiRequest("/api/llm/providers", { method: "POST", body: data });
export const updateProvider = (id, patch) =>
  apiRequest(`/api/llm/providers/${id}`, { method: "PATCH", body: patch });
export const deleteProvider = (id) =>
  apiRequest(`/api/llm/providers/${id}`, { method: "DELETE" });
export const getStatus = () => apiRequest("/api/llm/status");
