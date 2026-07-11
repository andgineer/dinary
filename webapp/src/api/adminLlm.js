import { apiRequest } from "./_request.js";

export const getStatus = () => apiRequest("/api/llm/status");

export const disableProvider = (name) =>
  apiRequest(`/api/llm/providers/${encodeURIComponent(name)}/disable`, { method: "POST" });

export const enableProvider = (name) =>
  apiRequest(`/api/llm/providers/${encodeURIComponent(name)}/enable`, { method: "POST" });
