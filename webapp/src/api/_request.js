export async function apiRequest(path, { method = "GET", body } = {}) {
  const init = { method };
  if (body !== undefined) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  }
  const resp = await fetch(path, init);
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    const e = new Error(err.detail || `HTTP ${resp.status}`);
    e.status = resp.status;
    throw e;
  }
  if (resp.status === 204 || resp.headers.get("content-length") === "0") return null;
  return resp.json();
}
