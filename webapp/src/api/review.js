import { apiRequest } from "./_request.js";

export function getReviewFeed({ page = 1, pageSize = 20 } = {}) {
  return apiRequest(`/api/receipts/review/feed?page=${page}&page_size=${pageSize}`);
}

export function getReviewCounts() {
  return apiRequest("/api/receipts/review/counts");
}
