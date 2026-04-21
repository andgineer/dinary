/**
 * Regression test for the default-group-and-category auto-selection bug
 * on PWA first paint.
 *
 * Observed symptom (reported by the operator):
 *   1. Open the PWA.
 *   2. The Envelope/Group dropdown visually shows "Еда" (the browser
 *      auto-selects the first option after ``populateGroupDropdown``
 *      wipes the placeholder).
 *   3. The Category dropdown still carries its HTML placeholder
 *      ``— select group first —``; opening it produces no real
 *      options.
 *   4. Only manually switching the Group to something else and back
 *      to "Еда" populates the Category list.
 *
 * Root cause:
 *   ``app.js`` uses ``DEFAULT_GROUP_NAME = "еда"`` (lowercase) but the
 *   seeded catalog stores the group as ``"Еда"`` (capital Cyrillic
 *   Е). ``catalog.js::findGroupByName`` is a strict equality lookup,
 *   so it returns ``null``. ``applyDefaultGroupAndCategory`` then
 *   skips the ``populateCategoryDropdown(catSelect, group.id)`` call
 *   and the Category ``<select>`` never gets real options. The
 *   ``change`` listener on the group select does call
 *   ``populateCategoryDropdown``, which is why toggling the group
 *   "unsticks" the dropdown.
 *
 * This file verifies two layers:
 *   a) ``findGroupByName`` matches the operator's default
 *      irrespective of case (the narrow root-cause unit test).
 *   b) The full init flow — ``populateGroupDropdown`` ->
 *      ``applyDefault`` -> ``populateCategoryDropdown`` — ends with
 *      the Category ``<select>`` carrying real category options
 *      (the integration test that would catch any regression even if
 *      the underlying matcher changes shape).
 *
 * Both tests fail today; both go green after the case-insensitive
 * match lands in ``findGroupByName``.
 */

// @vitest-environment happy-dom

import { beforeEach, describe, expect, it, vi } from "vitest";
import * as allure from "allure-js-commons";

const CATALOG_CACHE_KEY = "dinary:catalog:v1";

function installLocalStorageStub() {
  const store = new Map();
  const stub = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
    clear: () => store.clear(),
    key: (i) => Array.from(store.keys())[i] ?? null,
    get length() {
      return store.size;
    },
  };
  vi.stubGlobal("localStorage", stub);
  return stub;
}

// Mimics what ``GET /api/catalog`` returns after a fresh seed of the
// default catalog. Group names are capitalised (``"Еда"``); category
// names are lowercase (``"еда"``) — exact shape observed on the live
// server (see ``/api/catalog`` output during the deploy-and-reseed
// run).
const SEEDED_CATALOG = {
  catalog_version: 1,
  category_groups: [
    { id: 1, name: "Еда", sort_order: 1, is_active: true },
    { id: 2, name: "Жильё", sort_order: 2, is_active: true },
    { id: 3, name: "Транспорт", sort_order: 3, is_active: true },
  ],
  categories: [
    { id: 10, name: "еда", group: "Еда", group_id: 1, is_active: true },
    { id: 11, name: "ресторан", group: "Еда", group_id: 1, is_active: true },
    { id: 20, name: "квартира", group: "Жильё", group_id: 2, is_active: true },
    { id: 30, name: "бензин", group: "Транспорт", group_id: 3, is_active: true },
  ],
  events: [],
  tags: [],
};

beforeEach(() => {
  vi.restoreAllMocks();
  vi.resetModules();
  installLocalStorageStub();
  localStorage.setItem(
    CATALOG_CACHE_KEY,
    JSON.stringify({ ...SEEDED_CATALOG, etag: null }),
  );
});

async function importCatalog() {
  return await import("../../static/js/catalog.js");
}

describe("PWA default-group selection — case-insensitive group name match", () => {
  it("findGroupByName('еда') matches the capitalised 'Еда' group", async () => {
    await allure.feature("PWA default selection");

    const catalog = await importCatalog();
    catalog.replaceSnapshot(SEEDED_CATALOG);

    // The whole bug in one line: ``app.js`` asks for the group by
    // the constant ``DEFAULT_GROUP_NAME = "еда"`` (lowercase), the
    // catalog stores it as ``"Еда"`` (capital Cyrillic Е), and a
    // strict equality lookup returns ``null``. Case-insensitive
    // match (locale-aware for Cyrillic) is the contract we expect.
    const match = catalog.findGroupByName("еда");
    expect(match).not.toBeNull();
    expect(match.id).toBe(1);
    expect(match.name).toBe("Еда");
  });

  it("first-paint flow: category dropdown has real options after applyDefault", async () => {
    await allure.feature("PWA default selection");

    // Build a minimal HTML fixture with the same structure as
    // ``static/index.html`` — enough to let ``populateGroupDropdown``
    // / ``populateCategoryDropdown`` do their real work.
    document.body.innerHTML = `
      <select id="group"><option value="">— loading —</option></select>
      <select id="category">
        <option value="">— select group first —</option>
      </select>
    `;

    const catalog = await importCatalog();
    catalog.replaceSnapshot(SEEDED_CATALOG);

    const groupSelect = document.getElementById("group");
    const catSelect = document.getElementById("category");

    // Reproduce the two first-paint steps from ``app.js::init``:
    //   1) populate groups
    //   2) apply default (group "еда" -> populate categories for it)
    catalog.populateGroupDropdown(groupSelect);

    // Inlined reproduction of ``app.js::applyDefaultGroupAndCategory``
    // — kept local to the test so the test survives ``app.js``
    // refactors as long as the contract (select+populate on default
    // match) holds.
    const DEFAULT_GROUP_NAME = "еда";
    const group = catalog.findGroupByName(DEFAULT_GROUP_NAME);
    if (group) {
      groupSelect.value = String(group.id);
      catalog.populateCategoryDropdown(catSelect, group.id);
    }

    // ---- Guard 1: group select actually has the default selected ----
    expect(groupSelect.value).toBe("1");

    // ---- Guard 2: category select is populated, NOT stuck on the
    // placeholder. This is the directly-observed operator symptom.
    const realOptions = Array.from(catSelect.options).filter(
      (o) => o.value !== "",
    );
    expect(realOptions.length).toBeGreaterThan(0);
    const names = realOptions.map((o) => o.textContent).sort();
    expect(names).toEqual(["еда", "ресторан"]);

    // ---- Guard 3: the placeholder "— select group first —" has
    // been wiped. Its presence is the literal text the operator
    // sees in the bug report.
    expect(catSelect.innerHTML).not.toContain("select group first");
    expect(catSelect.innerHTML).not.toContain("Сначала выберите группу");
  });
});
