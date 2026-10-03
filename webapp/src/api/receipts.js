// Fetch wrapper for the receipt classification API.
// No Vue, no DOM, no store imports — pure network layer.

import { apiRequest } from "./_request.js";

const POST_RECEIPT_TIMEOUT_MS = 30_000;

export function getReceipt(id, { include = "" } = {}) {
  return include
    ? apiRequest(`/api/receipts/${id}?include=${encodeURIComponent(include)}`)
    : apiRequest(`/api/receipts/${id}`);
}

export function deleteReceipt(id) {
  return apiRequest(`/api/receipts/${id}`, { method: "DELETE" });
}

export function getReceiptQueue({ page = 1, pageSize = 20 } = {}) {
  return apiRequest(`/api/receipts/queue?page=${page}&page_size=${pageSize}`);
}

export function resolveReceipt(
  receiptId,
  { categoryId, tagIds = [], eventId = null, comment = "" },
) {
  return apiRequest(`/api/receipts/${receiptId}/resolve`, {
    method: "POST",
    body: { category_id: categoryId, tag_ids: tagIds, event_id: eventId, comment },
  });
}

export function postReceipt({ client_receipt_id, url }) {
  return apiRequest("/api/receipts", {
    method: "POST",
    body: { client_receipt_id, url },
    timeoutMs: POST_RECEIPT_TIMEOUT_MS,
  });
}
