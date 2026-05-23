// Pure fetch wrapper around the expenses API. No Vue, no DOM, no store
// imports — the catalog cache and offline queue are owned by the
// corresponding Pinia stores.

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
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), POST_EXPENSE_TIMEOUT_MS);
  try {
    const resp = await fetch("/api/expenses", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        client_expense_id,
        amount,
        currency,
        category_id,
        event_id: event_id ?? null,
        tag_ids: tag_ids ?? [],
        comment,
        expense_datetime,
      }),
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

export async function parseQr(url) {
  const resp = await fetch("/api/qr/parse", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${resp.status}`);
  }
  return resp.json();
}
