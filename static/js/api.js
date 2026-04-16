/**
 * API client — thin fetch wrapper with relative URLs.
 * All calls go to the same origin (FastAPI serves both API and PWA).
 */

export async function postExpense({
  expense_id,
  amount,
  currency,
  category,
  group,
  comment,
  date,
}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 30_000);
  try {
    const resp = await fetch("/api/expenses", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expense_id, amount, currency, category, group, comment, date }),
      signal: ctrl.signal,
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${resp.status}`);
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

export async function fetchCategories() {
  const resp = await fetch("/api/categories");
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${resp.status}`);
  }
  return resp.json();
}
