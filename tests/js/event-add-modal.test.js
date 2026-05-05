/**
 * Regression tests for the "+ New event" modal in ``catalog-add.js``.
 *
 * Two operator-reported bugs from 2026-04 land here:
 *
 *   1. The "Auto-fill when expense date matches" checkbox visually
 *      read as disabled (root cause was the global
 *      ``input { appearance: none }`` reset stripping the native
 *      box; fixed in ``style.css``). The JS surface for that bug is
 *      narrow — assert the DOM exposes a real, non-disabled
 *      checkbox so a future refactor cannot silently re-introduce
 *      ``disabled``.
 *
 *   2. Auto-tags were a free-text comma-separated input, so any
 *      typo or unknown name was rejected by the server with 422
 *      (``_require_known_tag_names`` in ``catalog_writer.py``). The
 *      fix replaces the text input with the same chip picker the
 *      main expense form uses (``populateTagsList`` /
 *      ``readSelectedTagIds`` from ``catalog.js``) so the operator
 *      can only pick names that already exist. These tests pin:
 *
 *      a) No text input for auto-tags exists anywhere in the modal.
 *      b) The chip picker shows exactly the *active* tags.
 *      c) Submit forwards selected tag NAMES (not ids) to
 *         ``adminAddEvent`` so the wire payload matches
 *         ``EventAddBody.auto_tags: list[str]``.
 *      d) Submit with no chip ticked sends ``auto_tags: null``
 *         (caller-supplied empty list semantics).
 *      e) Empty-catalog case surfaces an explicit "no tags yet"
 *         hint instead of a silent empty box.
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

const SEEDED_CATALOG_WITH_TAGS = {
  catalog_version: 1,
  category_groups: [],
  categories: [],
  events: [],
  tags: [
    { id: 1, name: "путешествия", is_active: true },
    { id: 2, name: "отпуск", is_active: true },
    // Inactive tag — must NOT show up in the chip picker (matches
    // the active-vs-inactive policy documented at the top of
    // ``catalog.js``).
    { id: 3, name: "ретро", is_active: false },
  ],
};

const SEEDED_CATALOG_NO_TAGS = {
  catalog_version: 1,
  category_groups: [],
  categories: [],
  events: [],
  tags: [],
};

// Capture every call into the admin API. Re-installed per test so
// assertions don't bleed across cases.
let adminAddEventMock;

beforeEach(() => {
  vi.restoreAllMocks();
  vi.resetModules();
  installLocalStorageStub();
  document.body.innerHTML = "";

  // Mock the entire ``api.js`` surface so neither the modal's submit
  // path nor ``catalog.js``'s reactivate / cache helpers try to hit
  // the network. ``adminAddEvent`` returns a minimal valid snapshot
  // so the post-submit ``replaceSnapshot`` call doesn't blow up
  // ``runSubmit``'s try/catch and turn the test into an indirect
  // failure ("submit succeeded but threw on snapshot replace").
  adminAddEventMock = vi.fn().mockResolvedValue({
    new_id: 42,
    status: "created",
    catalog_version: 2,
    category_groups: [],
    categories: [],
    events: [],
    tags: SEEDED_CATALOG_WITH_TAGS.tags,
  });
  vi.doMock("../../static/js/api.js", () => ({
    adminAddCategory: vi.fn(),
    adminAddEvent: adminAddEventMock,
    adminAddGroup: vi.fn(),
    adminAddTag: vi.fn(),
    adminDeactivateCategory: vi.fn(),
    adminDeactivateEvent: vi.fn(),
    adminDeactivateGroup: vi.fn(),
    adminDeactivateTag: vi.fn(),
    adminDeleteCategory: vi.fn(),
    adminDeleteEvent: vi.fn(),
    adminDeleteGroup: vi.fn(),
    adminDeleteTag: vi.fn(),
    adminReactivateCategory: vi.fn(),
    adminReactivateEvent: vi.fn(),
    adminReactivateGroup: vi.fn(),
    adminReactivateTag: vi.fn(),
    fetchCatalog: vi.fn(),
    replaceCachedCatalog: vi.fn(),
  }));
});

async function openModalWith(snapshot) {
  const catalog = await import("../../static/js/catalog.js");
  catalog.replaceSnapshot(snapshot);
  const { openAddEvent } = await import("../../static/js/catalog-add.js");
  // ``onAdded`` is unused in these tests — they assert at the
  // ``adminAddEvent`` boundary, not at the post-success callback.
  openAddEvent(() => {});
}

function fillRequiredFields() {
  // The modal pre-fills today's date in both date pickers, so the
  // only required field for submit is the name. Filling it via the
  // DOM keeps the tests OS-locale-independent.
  const nameEl = document.querySelector(".f-name");
  nameEl.value = "Jajce";
}

function clickChipByName(name) {
  // Each chip is a ``<label class="tag-chip"><input type=checkbox value=ID><span>NAME</span></label>``.
  // Find by label text and tick its inner checkbox directly so the
  // test does not depend on click-through-label behaviour (which
  // happy-dom historically had quirks with).
  const chips = Array.from(
    document.querySelectorAll(".f-auto-tags .tag-chip"),
  );
  const chip = chips.find((c) => c.querySelector("span")?.textContent === name);
  if (!chip) {
    throw new Error(
      `chip "${name}" not rendered; available: ${chips
        .map((c) => c.querySelector("span")?.textContent)
        .join(", ")}`,
    );
  }
  chip.querySelector("input[type=checkbox]").checked = true;
}

describe("openAddEvent — auto-tags chip picker (regression for free-text bug)", () => {
  it("renders chips for active tags only and exposes no text input", async () => {
    await allure.epic("PWA");
    await allure.feature("event-add modal");

    await openModalWith(SEEDED_CATALOG_WITH_TAGS);

    // Bug 2 regression: the old plain text input must be gone.
    expect(document.querySelector('input.f-auto-tags[type="text"]')).toBeNull();

    // Chip container exists, populated from ``populateTagsList`` —
    // the same renderer the main expense form uses, so the visual
    // contract (``.tags-list`` + ``.tag-chip``) is consistent across
    // forms and any future restyle of the chip picker carries here
    // automatically.
    const container = document.querySelector(".f-auto-tags");
    expect(container.classList.contains("tags-list")).toBe(true);

    const chipLabels = Array.from(
      container.querySelectorAll(".tag-chip span"),
    ).map((s) => s.textContent);
    // Inactive tag ``ретро`` must NOT appear; only the two active.
    expect(chipLabels.sort()).toEqual(["отпуск", "путешествия"]);

    // Each chip wraps a real checkbox keyed by tag id (so
    // ``readSelectedTagIds`` can read them back).
    const chipCbs = container.querySelectorAll('input[type="checkbox"]');
    expect(chipCbs.length).toBe(2);
    const ids = Array.from(chipCbs)
      .map((c) => Number(c.value))
      .sort((a, b) => a - b);
    expect(ids).toEqual([1, 2]);
  });

  it("submit forwards selected tag NAMES (not ids) to adminAddEvent", async () => {
    await allure.epic("PWA");
    await allure.feature("event-add modal");

    await openModalWith(SEEDED_CATALOG_WITH_TAGS);

    fillRequiredFields();
    clickChipByName("путешествия");
    document.querySelector(".f-auto-attach").checked = true;

    document.querySelector(".add-modal-submit").click();
    // Wait one microtask tick for the async submit promise chain.
    await new Promise((r) => setTimeout(r, 0));

    expect(adminAddEventMock).toHaveBeenCalledTimes(1);
    const payload = adminAddEventMock.mock.calls[0][0];
    expect(payload.name).toBe("Jajce");
    expect(payload.auto_attach_enabled).toBe(true);
    // Wire format expects names; sending ids would 422 on the
    // server (``_require_known_tag_names`` looks up by ``tags.name``).
    expect(payload.auto_tags).toEqual(["путешествия"]);
  });

  it("submit with no chip ticked sends auto_tags: null", async () => {
    await allure.epic("PWA");
    await allure.feature("event-add modal");

    await openModalWith(SEEDED_CATALOG_WITH_TAGS);
    fillRequiredFields();

    document.querySelector(".add-modal-submit").click();
    await new Promise((r) => setTimeout(r, 0));

    expect(adminAddEventMock).toHaveBeenCalledTimes(1);
    const payload = adminAddEventMock.mock.calls[0][0];
    // ``null`` (not ``[]``) so the server-side default kicks in;
    // matches the explicit ternary in ``openAddEvent``'s submit
    // handler and the existing ``api.js::adminAddEvent`` shape.
    expect(payload.auto_tags).toBeNull();
    expect(payload.auto_attach_enabled).toBe(false);
  });

  it("empty catalog: hides chip box and shows the explicit 'no tags yet' hint", async () => {
    await allure.epic("PWA");
    await allure.feature("event-add modal");

    await openModalWith(SEEDED_CATALOG_NO_TAGS);

    const container = document.querySelector(".f-auto-tags");
    const hint = document.querySelector(".f-auto-tags-empty");
    // Container collapsed (no useless empty strip), hint visible
    // with the actionable text. Operator on a fresh catalog needs
    // the explicit pointer at the "+ New" tag flow — without it the
    // bare empty box reads exactly as the original "broken" bug.
    expect(container.style.display).toBe("none");
    expect(hint.style.display).toBe("");
    expect(hint.textContent).toMatch(/No tags exist yet/);
  });
});

describe("openAddEvent — auto_attach checkbox (regression for visual-disabled bug)", () => {
  it("renders the checkbox without a disabled attribute and as togglable", async () => {
    await allure.epic("PWA");
    await allure.feature("event-add modal");

    await openModalWith(SEEDED_CATALOG_NO_TAGS);

    const cb = document.querySelector(".f-auto-attach");
    // Bug 1 regression: the checkbox must be a real, enabled DOM
    // control. The visual-only "looks disabled" state was a CSS
    // bug (global ``input { appearance: none }`` reset), but a
    // future refactor that adds ``disabled`` attribute or moves
    // off ``<input type="checkbox">`` would also break the
    // operator flow — pin the contract at the DOM level here.
    expect(cb).not.toBeNull();
    expect(cb.tagName).toBe("INPUT");
    expect(cb.type).toBe("checkbox");
    expect(cb.disabled).toBe(false);
    expect(cb.hasAttribute("disabled")).toBe(false);

    // And it actually toggles.
    expect(cb.checked).toBe(false);
    cb.click();
    expect(cb.checked).toBe(true);
  });
});

// ``CATALOG_CACHE_KEY`` is referenced by the localStorage-stub
// pattern shared with ``default-group-selection.test.js`` —
// declared at module scope to make the parity obvious even though
// these tests don't seed the cache.
void CATALOG_CACHE_KEY;
