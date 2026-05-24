import { apiRequest } from "./_request.js";

export function getReviewFeed({ page = 1, pageSize = 20, doubtfulOnly = true } = {}) {
  return apiRequest(`/api/rules/feed?page=${page}&page_size=${pageSize}&doubtful_only=${doubtfulOnly}`);
}

export function getExpensesFeed({ page = 1, pageSize = 20 } = {}) {
  return apiRequest(`/api/expenses?page=${page}&page_size=${pageSize}`);
}

export function getReviewCounts() {
  return apiRequest("/api/rules/counts");
}

export function confirmAllRules(ruleIds) {
  return apiRequest("/api/rules/confirm-all", {
    method: "POST",
    body: { rule_ids: ruleIds },
  });
}

export function approveRule(ruleId, categoryId) {
  return apiRequest(`/api/rules/${ruleId}/category`, {
    method: "PATCH",
    body: { category_id: categoryId },
  });
}
