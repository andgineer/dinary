// Pure fetch wrappers around the currency picker HTTP surface.
// The Pinia store talks to these helpers; UI components never call
// fetch directly. The PWA only manages the saved-codes list — rate
// conversion happens server-side at expense-write time, so there is
// intentionally no client-side rate API here.

async function jsonRequest(path, { method = "GET", body } = {}) {
  const init = { method, headers: { "Content-Type": "application/json" } };
  if (body !== undefined) init.body = JSON.stringify(body);
  const resp = await fetch(path, init);
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    const e = new Error(err.detail || `HTTP ${resp.status}`);
    e.status = resp.status;
    throw e;
  }
  return resp.json();
}

/**
 * GET /api/currencies. Returns ``{codes, default_code}``.
 */
export async function fetchCurrencies() {
  return jsonRequest("/api/currencies");
}

/**
 * POST /api/currencies. Adds a single ISO-4217 code (server normalises to
 * uppercase). Returns the updated ``{codes, default_code}`` snapshot.
 */
export async function addCurrency(code) {
  if (typeof code !== "string" || code.trim().length === 0) {
    throw new Error("addCurrency: code is required");
  }
  return jsonRequest("/api/currencies", {
    method: "POST",
    body: { code: code.trim().toUpperCase() },
  });
}

/**
 * DELETE /api/currencies/{code}. Returns the updated snapshot. The server
 * blocks deletion of ``default_code`` with HTTP 409.
 */
export async function deleteCurrency(code) {
  if (typeof code !== "string" || code.trim().length === 0) {
    throw new Error("deleteCurrency: code is required");
  }
  const upper = code.trim().toUpperCase();
  return jsonRequest(`/api/currencies/${encodeURIComponent(upper)}`, {
    method: "DELETE",
  });
}
