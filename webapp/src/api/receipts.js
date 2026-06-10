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

export async function postReceipt({ client_receipt_id, url }) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), POST_RECEIPT_TIMEOUT_MS);
  try {
    const resp = await fetch("/api/receipts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ client_receipt_id, url }),
      signal: ctrl.signal,
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      const e = new Error(err.detail || `HTTP ${resp.status}`);
      e.status = resp.status;
      throw e;
    }
    return resp.json();
  } finally {
    clearTimeout(timer);
  }
}
