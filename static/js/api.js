/**
 * API client — thin fetch wrapper with relative URLs.
 *
 * Phase 2:
 *  - ``postExpense`` now sends a 3D body with catalog primary keys
 *    (category_id, event_id, tag_ids). The server resolves 3D->2D at
 *    drain time from runtime_mapping (the curated ``map`` worksheet
 *    tab), so the client no longer picks a sheet target.
 *  - ``fetchCatalog`` hits the single /api/catalog endpoint. It wraps
 *    a localStorage cache keyed by catalog_version and round-trips
 *    with If-None-Match => 304 on every refresh. The steady state is
 *    zero catalog GETs per expense because POST /api/expenses returns
 *    the current catalog_version and callers only refetch when the
 *    server-side version differs.
 *  - Admin mutations (add group/category/event/tag) go through the
 *    ``Authorization: Bearer <token>`` flow. The token lives in
 *    localStorage["admin_api_token"]; empty => admin UI hidden.
 */

const CATALOG_CACHE_KEY = "dinary:catalog:v1";
const ADMIN_TOKEN_KEY = "dinary:admin_token";

// Mirror of ``dinary.api.catalog._etag_for``. The server no longer
// ships an ``etag`` field in the catalog response body; the value is
// a pure function of ``catalog_version``, so the client derives it
// locally every time it needs to send ``If-None-Match``. Any change
// to the server-side format must stay in lockstep with this helper.
function etagFor(catalogVersion) {
  return `W/"catalog-v${catalogVersion}"`;
}

function getAdminToken() {
  return localStorage.getItem(ADMIN_TOKEN_KEY) || "";
}

export function hasAdminToken() {
  return getAdminToken().length > 0;
}

export function setAdminToken(token) {
  if (token) localStorage.setItem(ADMIN_TOKEN_KEY, token);
  else localStorage.removeItem(ADMIN_TOKEN_KEY);
}

function adminHeaders() {
  const token = getAdminToken();
  const h = { "Content-Type": "application/json" };
  if (token) h["Authorization"] = `Bearer ${token}`;
  return h;
}

export async function postExpense({
  client_expense_id,
  amount,
  currency,
  category_id,
  event_id,
  tag_ids,
  comment,
  date,
}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 30_000);
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
        date,
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

// ---------------------------------------------------------------------------
// Catalog cache (ETag + localStorage)
// ---------------------------------------------------------------------------

function readCachedCatalog() {
  try {
    const raw = localStorage.getItem(CATALOG_CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    // ``catalog_version`` is the one required field — ``etag`` used
    // to live here too but is now derived client-side via
    // ``etagFor``, so an absent etag field is no longer an
    // invalidation signal.
    if (
      !parsed ||
      typeof parsed !== "object" ||
      typeof parsed.catalog_version !== "number"
    ) {
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

function writeCachedCatalog(snapshot) {
  try {
    // Always normalise through ``toCatalogSnapshot`` so the cache has
    // exactly the fields ``GET /api/catalog`` returns, regardless of
    // whether the caller handed us a GET body or an
    // ``AdminCatalogResponse`` (which carries admin-only
    // ``new_id`` / ``status``). Centralising the strip here means
    // any future ``writeCachedCatalog`` caller is safe by default —
    // the earlier cache-leak bug went unnoticed precisely because
    // the strip lived at a higher layer than the actual write.
    localStorage.setItem(
      CATALOG_CACHE_KEY,
      JSON.stringify(toCatalogSnapshot(snapshot)),
    );
  } catch {
    // Quota or privacy-mode storage failure: next call will refetch.
  }
}

/**
 * Fetch the full catalog snapshot. Uses localStorage cache + ETag to
 * avoid downloading the full payload when nothing changed. Callers
 * should call this:
 *   - once on app init;
 *   - again after a POST /api/expenses response where
 *     response.catalog_version differs from the cached version.
 */
export async function fetchCatalog() {
  const cached = readCachedCatalog();
  const headers = {};
  if (cached) headers["If-None-Match"] = etagFor(cached.catalog_version);
  const resp = await fetch("/api/catalog", { headers });
  if (resp.status === 304 && cached) return cached;
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${resp.status}`);
  }
  const snapshot = await resp.json();
  writeCachedCatalog(snapshot);
  return snapshot;
}

export function cachedCatalogVersion() {
  const cached = readCachedCatalog();
  return cached ? cached.catalog_version : -1;
}

export function replaceCachedCatalog(snapshot) {
  // Thin alias around ``writeCachedCatalog``; the admin-field strip
  // lives inside ``writeCachedCatalog`` so every write path is safe
  // without the caller having to remember which helper to use.
  writeCachedCatalog(snapshot);
}

// ---------------------------------------------------------------------------
// Admin catalog mutations
// ---------------------------------------------------------------------------

async function postAdmin(path, body) {
  const resp = await fetch(path, {
    method: "POST",
    headers: adminHeaders(),
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    // 401 / 403 means the token in localStorage is missing or wrong.
    // Clear it so the next add-modal call re-prompts the operator
    // instead of replaying the same bad token forever. 503 means the
    // admin API is disabled server-side (empty DINARY_ADMIN_API_TOKEN);
    // we keep whatever the user pasted, since the server-side config
    // is the problem, not the token.
    if (resp.status === 401 || resp.status === 403) {
      setAdminToken("");
    }
    const e = new Error(err.detail || `HTTP ${resp.status}`);
    e.status = resp.status;
    throw e;
  }
  const snapshot = await resp.json();
  // Admin responses include the full post-mutation snapshot plus two
  // admin-only fields (``new_id``, ``status``); ``writeCachedCatalog``
  // strips those internally so the localStorage cache has the same
  // shape as a ``GET /api/catalog`` body. We still return the full
  // response to the caller so the PWA can surface ``status`` and
  // ``new_id`` (e.g. focusing the freshly-added option in a dropdown).
  writeCachedCatalog(snapshot);
  return snapshot;
}

function toCatalogSnapshot(adminResponse) {
  // Keep in sync with ``build_catalog_snapshot`` on the server: the
  // admin response envelope wraps the same dict-of-lists plus two
  // admin-only fields (``new_id``, ``status``) that the PWA cache
  // doesn't need. Destructure by the *server's* key names
  // (``category_groups`` — not ``groups``) so the cached snapshot
  // has the shape ``catalog.js::getGroups()`` et al. expect.
  // ``etag`` is no longer part of the server response body; the
  // PWA derives it from ``catalog_version`` via ``etagFor`` at the
  // exact moment it needs to send ``If-None-Match``.
  const {
    catalog_version,
    category_groups,
    categories,
    events,
    tags,
  } = adminResponse;
  return { catalog_version, category_groups, categories, events, tags };
}

export async function adminAddGroup({ name, sort_order }) {
  return postAdmin("/api/admin/catalog/groups", { name, sort_order: sort_order ?? null });
}

export async function adminAddCategory({ name, group_id, sheet_name, sheet_group }) {
  return postAdmin("/api/admin/catalog/categories", {
    name,
    group_id,
    sheet_name: sheet_name ?? null,
    sheet_group: sheet_group ?? null,
  });
}

export async function adminAddEvent({ name, date_from, date_to, auto_attach_enabled }) {
  return postAdmin("/api/admin/catalog/events", {
    name,
    date_from,
    date_to,
    auto_attach_enabled: auto_attach_enabled ?? false,
  });
}

export async function adminAddTag({ name }) {
  return postAdmin("/api/admin/catalog/tags", { name });
}
