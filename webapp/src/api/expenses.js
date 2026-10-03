// Pure fetch wrapper around the expenses API. No Vue, no DOM, no store
// imports — the catalog cache and offline queue are owned by the
// corresponding Pinia stores.

import { apiRequest } from "./_request.js";

const POST_EXPENSE_TIMEOUT_MS = 30_000;

export async function postExpense({
  client_expense_id,
  amount,
  currency,
  category_id,
  event_id,
  tag_ids,
  comment,
  expense_datetime,
}) {
  return apiRequest("/api/expenses", {
    method: "POST",
    body: {
      client_expense_id,
      amount,
      currency,
      category_id,
      event_id: event_id ?? null,
      tag_ids: tag_ids ?? [],
      comment,
      expense_datetime,
    },
    timeoutMs: POST_EXPENSE_TIMEOUT_MS,
  });
}

export function deleteExpense(id) {
  return apiRequest(`/api/expenses/${id}`, { method: "DELETE" });
}
