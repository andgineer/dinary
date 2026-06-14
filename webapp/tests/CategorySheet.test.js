import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { nextTick } from "vue";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import CategorySheet from "../src/components/CategorySheet.vue";
import { useCatalogStore } from "../src/stores/catalog.js";
import { useToastStore } from "../src/stores/toast.js";
import * as catalogApi from "../src/api/catalog.js";

beforeEach(async () => {
  await allure.epic("Expenses");
  await allure.feature("Frontend");
  await allure.story("CategorySheet");
});

const TELEPORT_STUB = { props: ["to"], template: "<div><slot /></div>" };

const VISIBLE_CATEGORIES = [
  { id: 10, code: "groceries", name: "Groceries", group_id: 1, group_name: "Food", group_sort_order: 1, group_code: "food" },
  { id: 11, code: "cafe", name: "Cafe", group_id: 1, group_name: "Food", group_sort_order: 1, group_code: "food" },
  { id: 20, code: "taxi", name: "Taxi", group_id: 2, group_name: "Transport", group_sort_order: 2, group_code: "transport" },
];

const TEMPLATES = [
  {
    code: "simple",
    names: { en: "Simple", ru: "Простой" },
    taglines: { en: "Basics only", ru: "Только основное" },
    groups: [],
  },
  {
    code: "travel",
    names: { en: "Travel", ru: "Путешествия" },
    taglines: { en: "For frequent travelers", ru: "Для тех, кто часто путешествует" },
    groups: [],
  },
];

function mountSheet(props = {}) {
  const pinia = createPinia();
  setActivePinia(pinia);
  const store = useCatalogStore(pinia);
  store.visibleCategories = VISIBLE_CATEGORIES.map((c) => ({ ...c }));
  store.visibleCategoriesVersion = 1;
  const w = mount(CategorySheet, {
    props: { open: true, suggestions: [], ...props },
    global: { plugins: [pinia], stubs: { Teleport: TELEPORT_STUB } },
  });
  return { w, pinia, store };
}

beforeEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  // CategorySheet loads the template catalog (for the bottom bar's active-set
  // name) on every open — mock it so unrelated tests never hit the network.
  vi.spyOn(catalogApi, "listTemplates").mockResolvedValue(TEMPLATES);
});

const SEARCH_DEBOUNCE_MS = 300;

async function search(w, text) {
  await w.find(".search-input").setValue(text);
  await new Promise((resolve) => setTimeout(resolve, SEARCH_DEBOUNCE_MS + 50));
  await nextTick();
}

describe("CategorySheet — grouped list (empty query)", () => {
  it("shows category groups from visibleCategories, ordered by group_sort_order", () => {
    const { w } = mountSheet();
    const groups = w.findAll('[data-testid="category-group"]');
    expect(groups).toHaveLength(2);
    expect(groups[0].find(".group-label").text()).toBe("Food");
    expect(groups[1].find(".group-label").text()).toBe("Transport");
    expect(groups[0].findAll(".cat-btn").map((b) => b.text())).toEqual(["Groceries", "Cafe"]);
  });

  it("shows flat results and hides groups when query is non-empty", async () => {
    vi.spyOn(catalogApi, "searchCategories").mockResolvedValue([]);
    const { w } = mountSheet();
    await search(w, "gro");
    expect(w.find('[data-testid="flat-results"]').exists()).toBe(true);
    expect(w.find('[data-testid="category-group"]').exists()).toBe(false);
  });

  it("clear button resets query and shows groups again", async () => {
    vi.spyOn(catalogApi, "searchCategories").mockResolvedValue([]);
    const { w } = mountSheet();
    await search(w, "taxi");
    expect(w.find(".clear-btn").exists()).toBe(true);
    await w.find(".clear-btn").trigger("click");
    expect(w.find('[data-testid="flat-results"]').exists()).toBe(false);
    expect(w.find('[data-testid="category-group"]').exists()).toBe(true);
  });
});

describe("CategorySheet — suggestion tap emits select", () => {
  it("emits select with suggestion id when pill is clicked", async () => {
    const sug = [{ id: 10, name: "groceries" }];
    const { w } = mountSheet({ suggestions: sug });
    const pill = w.find('[data-testid="suggestion-pills"] .cat-btn');
    await pill.trigger("click");
    expect(w.emitted("select")?.[0]).toEqual([10]);
    expect(w.emitted("close")).toBeTruthy();
  });
});

describe("CategorySheet — grid tap emits select", () => {
  it("emits select with category id when grid button is clicked", async () => {
    const { w } = mountSheet();
    const btn = w.find('[data-testid="category-group"] .cat-btn');
    await btn.trigger("click");
    expect(w.emitted("select")?.[0]).toEqual([10]);
    expect(w.emitted("close")).toBeTruthy();
  });
});

describe("CategorySheet — sticky search bar", () => {
  it("search-wrap is outside sheet-body (not inside scrollable area)", () => {
    const { w } = mountSheet();
    const sheetBody = w.find(".sheet-body");
    expect(sheetBody.find(".search-wrap").exists()).toBe(false);
    expect(w.find(".sheet .search-wrap").exists()).toBe(true);
  });

  it("search-wrap renders as a direct child of sheet (outside scroll container)", () => {
    const { w } = mountSheet();
    const sheetEl = w.find(".sheet");
    const directChildren = sheetEl.element.children;
    const childClasses = Array.from(directChildren).map((el) => el.className);
    expect(childClasses.some((c) => c.includes("search-wrap"))).toBe(true);
  });

  it("resets sheet-body scrollTop to 0 when sheet is opened", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const store = useCatalogStore(pinia);
    store.visibleCategories = VISIBLE_CATEGORIES.map((c) => ({ ...c }));
    store.visibleCategoriesVersion = 1;
    const w = mount(CategorySheet, {
      props: { open: false, suggestions: [] },
      global: { plugins: [pinia], stubs: { Teleport: TELEPORT_STUB } },
    });
    await w.setProps({ open: true });
    await nextTick();
    await nextTick();
    expect(w.find(".sheet-body").element.scrollTop).toBe(0);
  });
});

describe("CategorySheet — Escape key", () => {
  it("clears query when Escape pressed with non-empty query", async () => {
    vi.spyOn(catalogApi, "searchCategories").mockResolvedValue([]);
    const { w } = mountSheet();
    await search(w, "taxi");
    expect(w.find('[data-testid="flat-results"]').exists()).toBe(true);
    await w.find(".search-input").trigger("keydown", { key: "Escape" });
    expect(w.find('[data-testid="flat-results"]').exists()).toBe(false);
    expect(w.find('[data-testid="category-group"]').exists()).toBe(true);
  });

  it("emits close when Escape pressed with empty query", async () => {
    const { w } = mountSheet();
    await w.find(".search-input").trigger("keydown", { key: "Escape" });
    expect(w.emitted("close")).toBeTruthy();
  });
});

describe("CategorySheet — search: in-set results", () => {
  it("shows matches already in the set as flat-items with group › name", async () => {
    vi.spyOn(catalogApi, "searchCategories").mockResolvedValue([
      { id: 10, code: "groceries", name: "Groceries", is_active: true, is_hidden: false },
      { id: 20, code: "taxi", name: "Taxi", is_active: true, is_hidden: false },
    ]);
    const { w } = mountSheet();
    await search(w, "a");

    const items = w.findAll(".flat-item");
    expect(items).toHaveLength(2);
    expect(items[0].text()).toContain("Food");
    expect(items[0].text()).toContain("Groceries");
    expect(items[1].text()).toContain("Transport");
    expect(items[1].text()).toContain("Taxi");
    expect(w.find('[data-testid="addable-section"]').exists()).toBe(false);
  });

  it("emits select and close when an in-set flat result is tapped", async () => {
    vi.spyOn(catalogApi, "searchCategories").mockResolvedValue([
      { id: 20, code: "taxi", name: "Taxi", is_active: true, is_hidden: false },
    ]);
    const { w } = mountSheet();
    await search(w, "taxi");

    await w.find(".flat-item").trigger("click");
    expect(w.emitted("select")?.[0]).toEqual([20]);
    expect(w.emitted("close")).toBeTruthy();
  });

  it("shows 'No matches' when nothing matches", async () => {
    vi.spyOn(catalogApi, "searchCategories").mockResolvedValue([]);
    const { w } = mountSheet();
    await search(w, "zzz");

    expect(w.find(".no-results").text()).toBe("No matches");
    expect(w.find('[data-testid="addable-section"]').exists()).toBe(false);
  });

  it("drops a stale response that resolves after the query changed", async () => {
    let resolveFirst;
    vi.spyOn(catalogApi, "searchCategories").mockImplementation((q) => {
      if (q === "a") return new Promise((resolve) => { resolveFirst = resolve; });
      return Promise.resolve([
        { id: 20, code: "taxi", name: "Taxi", is_active: true, is_hidden: false },
      ]);
    });
    const { w } = mountSheet();

    await search(w, "a");
    await search(w, "ab");

    resolveFirst([
      { id: 10, code: "groceries", name: "Groceries", is_active: true, is_hidden: false },
    ]);
    await flushPromises();
    await nextTick();

    const items = w.findAll(".flat-item");
    expect(items).toHaveLength(1);
    expect(items[0].text()).toContain("Taxi");
  });
});

describe("CategorySheet — search: 'Not in your set' addable section", () => {
  it("renders inactive and hidden matches in a fenced addable section", async () => {
    vi.spyOn(catalogApi, "searchCategories").mockResolvedValue([
      { id: 30, code: "concerts", name: "Concerts", is_active: false, is_hidden: false },
      { id: 31, code: "coworking", name: "Coworking", is_active: true, is_hidden: true },
    ]);
    const { w } = mountSheet();
    await search(w, "co");

    const section = w.find('[data-testid="addable-section"]');
    expect(section.exists()).toBe(true);
    expect(section.text()).toContain("Not in your set");
    const rows = section.findAll(".addable-item");
    expect(rows).toHaveLength(2);
    expect(rows[0].text()).toContain("Concerts");
    expect(rows[1].text()).toContain("Coworking");
    expect(rows[1].find(".hidden-tag").exists()).toBe(true);
    expect(rows[0].find(".hidden-tag").exists()).toBe(false);
  });

  it("omits the section when every match is already in the set", async () => {
    vi.spyOn(catalogApi, "searchCategories").mockResolvedValue([
      { id: 10, code: "groceries", name: "Groceries", is_active: true, is_hidden: false },
    ]);
    const { w } = mountSheet();
    await search(w, "gro");

    expect(w.find('[data-testid="addable-section"]').exists()).toBe(false);
  });

  it("activating an inactive result calls activateCategory, toasts, and selects it", async () => {
    vi.spyOn(catalogApi, "searchCategories").mockResolvedValue([
      { id: 30, code: "concerts", name: "Concerts", is_active: false, is_hidden: false },
    ]);
    const activate = vi
      .spyOn(catalogApi, "activateCategory")
      .mockResolvedValue({ catalog_version: 2 });
    vi.spyOn(catalogApi, "getCategories").mockResolvedValue({
      catalog_version: 2,
      categories: [
        ...VISIBLE_CATEGORIES,
        { id: 30, code: "concerts", name: "Concerts", group_id: 3, group_name: "Leisure", group_sort_order: 3, group_code: "leisure" },
      ],
    });
    vi.spyOn(catalogApi, "getActiveTemplate").mockResolvedValue({ active_template: "simple" });

    const { w } = mountSheet();
    await search(w, "co");

    await w.find(".addable-item").trigger("click");
    await flushPromises();

    expect(activate).toHaveBeenCalledWith("concerts");
    const toast = useToastStore();
    expect(toast.message).toBe('"Concerts" added to your set');
    expect(toast.type).toBe("info");
    expect(w.emitted("select")?.[0]).toEqual([30]);
    expect(w.emitted("close")).toBeTruthy();
  });

  it("on the 3rd out-of-set activation, sets the persistent nudge flag instead of the 'added' toast", async () => {
    localStorage.setItem(
      "dinary:catalog:oosActivations",
      JSON.stringify([Date.now(), Date.now()]),
    );
    vi.spyOn(catalogApi, "searchCategories").mockResolvedValue([
      { id: 30, code: "concerts", name: "Concerts", is_active: false, is_hidden: false },
    ]);
    vi.spyOn(catalogApi, "activateCategory").mockResolvedValue({ catalog_version: 2 });
    vi.spyOn(catalogApi, "getCategories").mockResolvedValue({
      catalog_version: 2,
      categories: [
        ...VISIBLE_CATEGORIES,
        { id: 30, code: "concerts", name: "Concerts", group_id: 3, group_name: "Leisure", group_sort_order: 3, group_code: "leisure" },
      ],
    });
    vi.spyOn(catalogApi, "getActiveTemplate").mockResolvedValue({ active_template: "simple" });

    const { w, store } = mountSheet();
    await search(w, "co");

    await w.find(".addable-item").trigger("click");
    await flushPromises();

    expect(store.showSetNudge).toBe(true);
    const toast = useToastStore();
    expect(toast.message).not.toBe('"Concerts" added to your set');
  });

  it("activating a hidden result calls unhideCategory", async () => {
    vi.spyOn(catalogApi, "searchCategories").mockResolvedValue([
      { id: 11, code: "cafe", name: "Cafe", is_active: true, is_hidden: true },
    ]);
    const unhide = vi
      .spyOn(catalogApi, "unhideCategory")
      .mockResolvedValue({ catalog_version: 2 });
    vi.spyOn(catalogApi, "getCategories").mockResolvedValue({
      catalog_version: 2,
      categories: VISIBLE_CATEGORIES,
    });
    vi.spyOn(catalogApi, "getActiveTemplate").mockResolvedValue({ active_template: "simple" });

    const { w } = mountSheet();
    await search(w, "cafe");

    await w.find(".addable-item").trigger("click");
    await flushPromises();

    expect(unhide).toHaveBeenCalledWith("cafe");
    expect(w.emitted("select")?.[0]).toEqual([11]);
  });

  it("blocks activation while offline and keeps the sheet open", async () => {
    Object.defineProperty(navigator, "onLine", { value: false, configurable: true });
    try {
      vi.spyOn(catalogApi, "searchCategories").mockResolvedValue([
        { id: 30, code: "concerts", name: "Concerts", is_active: false, is_hidden: false },
      ]);
      const activate = vi.spyOn(catalogApi, "activateCategory");

      const { w } = mountSheet();
      await search(w, "co");

      await w.find(".addable-item").trigger("click");
      await flushPromises();

      expect(activate).not.toHaveBeenCalled();
      const toast = useToastStore();
      expect(toast.message).toBe("Not available offline");
      expect(toast.type).toBe("error");
      expect(w.emitted("select")).toBeFalsy();
      expect(w.emitted("close")).toBeFalsy();
    } finally {
      Object.defineProperty(navigator, "onLine", { value: true, configurable: true });
    }
  });

  it("an activated category still absent from visibleCategories (no group) shows inline as без группы", async () => {
    vi.spyOn(catalogApi, "searchCategories").mockResolvedValue([
      { id: 40, code: "u_misc", name: "Misc", is_active: false, is_hidden: false },
    ]);
    vi.spyOn(catalogApi, "activateCategory").mockResolvedValue({ catalog_version: 2 });
    vi.spyOn(catalogApi, "getCategories").mockResolvedValue({
      catalog_version: 2,
      categories: VISIBLE_CATEGORIES,
    });
    vi.spyOn(catalogApi, "getActiveTemplate").mockResolvedValue({ active_template: "simple" });

    const { w } = mountSheet();
    await search(w, "misc");

    await w.find(".addable-item").trigger("click");
    await flushPromises();

    // Stays open — not enough to appear in the grouped list (NULL group_id).
    expect(w.emitted("select")).toBeFalsy();
    expect(w.find('[data-testid="addable-section"]').exists()).toBe(false);
    const item = w.find(".flat-item");
    expect(item.text()).toContain("без группы / ungrouped");
    expect(item.text()).toContain("Misc");

    await item.trigger("click");
    expect(w.emitted("select")?.[0]).toEqual([40]);
    expect(w.emitted("close")).toBeTruthy();
  });
});

describe("CategorySheet — Manage mode (§4)", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("toggle switches the body to the managed view and back", async () => {
    vi.spyOn(catalogApi, "listTemplates").mockResolvedValue(TEMPLATES);
    const { w, store } = mountSheet();
    store.activeTemplate = "simple";

    await w.find('[data-testid="manage-toggle"]').trigger("click");
    await flushPromises();
    expect(w.find('[data-testid="manage-view"]').exists()).toBe(true);
    expect(w.find('[data-testid="category-group"]').exists()).toBe(false);

    await w.find('[data-testid="manage-toggle"]').trigger("click");
    expect(w.find('[data-testid="manage-view"]').exists()).toBe(false);
    expect(w.find('[data-testid="category-group"]').exists()).toBe(true);
  });

  it("hides a category, removing it from the grouped picker", async () => {
    vi.spyOn(catalogApi, "listTemplates").mockResolvedValue(TEMPLATES);
    const hide = vi.spyOn(catalogApi, "hideCategory").mockResolvedValue({ catalog_version: 2 });
    vi.spyOn(catalogApi, "getCategories").mockResolvedValue({
      catalog_version: 2,
      categories: VISIBLE_CATEGORIES.filter((c) => c.code !== "groceries"),
    });
    vi.spyOn(catalogApi, "getActiveTemplate").mockResolvedValue({ active_template: "simple" });

    const { w, store } = mountSheet();
    store.activeTemplate = "simple";
    await w.find('[data-testid="manage-toggle"]').trigger("click");
    await flushPromises();

    const rows = () => w.findAll('[data-testid="manage-category-row"]');
    const groceriesRow = rows().find((r) => r.text().includes("Groceries"));
    await groceriesRow.find('[aria-label="Hide Groceries"]').trigger("click");
    await flushPromises();

    expect(hide).toHaveBeenCalledWith("groceries");
    expect(rows().some((r) => r.text().includes("Groceries"))).toBe(false);
  });

  it("renames a category, updating its label without changing its code", async () => {
    vi.spyOn(catalogApi, "listTemplates").mockResolvedValue(TEMPLATES);
    const rename = vi.spyOn(catalogApi, "renameCategory").mockResolvedValue({ catalog_version: 2 });
    vi.spyOn(catalogApi, "getCategories").mockResolvedValue({
      catalog_version: 2,
      categories: VISIBLE_CATEGORIES.map((c) =>
        c.code === "groceries" ? { ...c, name: "Groceries 2" } : c,
      ),
    });
    vi.spyOn(catalogApi, "getActiveTemplate").mockResolvedValue({ active_template: "simple" });

    const { w, store } = mountSheet();
    store.activeTemplate = "simple";
    await w.find('[data-testid="manage-toggle"]').trigger("click");
    await flushPromises();

    const rows = () => w.findAll('[data-testid="manage-category-row"]');
    const groceriesRow = rows().find((r) => r.text().includes("Groceries"));
    await groceriesRow.find('[aria-label="Rename Groceries"]').trigger("click");

    const editingRow = rows().find((r) => r.find(".manage-rename-input").exists());
    await editingRow.find(".manage-rename-input").setValue("Groceries 2");
    await editingRow.find('[aria-label="Save Groceries"]').trigger("click");
    await flushPromises();

    expect(rename).toHaveBeenCalledWith("groceries", "Groceries 2");
    expect(rows().some((r) => r.text().includes("Groceries 2"))).toBe(true);
  });

  it("moves a category to another group via the move select", async () => {
    vi.spyOn(catalogApi, "listTemplates").mockResolvedValue(TEMPLATES);
    const move = vi.spyOn(catalogApi, "moveCategory").mockResolvedValue({ catalog_version: 2 });
    vi.spyOn(catalogApi, "getCategories").mockResolvedValue({
      catalog_version: 2,
      categories: VISIBLE_CATEGORIES.map((c) =>
        c.code === "groceries"
          ? { ...c, group_id: 2, group_name: "Transport", group_sort_order: 2, group_code: "transport" }
          : c,
      ),
    });
    vi.spyOn(catalogApi, "getActiveTemplate").mockResolvedValue({ active_template: "simple" });

    const { w, store } = mountSheet();
    store.activeTemplate = "simple";
    await w.find('[data-testid="manage-toggle"]').trigger("click");
    await flushPromises();

    await w.find('[aria-label="Move Groceries to group"]').setValue("transport");
    await flushPromises();

    expect(move).toHaveBeenCalledWith("groceries", "transport");
    const groups = w.findAll('[data-testid="manage-group"]');
    const transport = groups.find((g) => g.find(".group-label").text() === "Transport");
    const food = groups.find((g) => g.find(".group-label").text() === "Food");
    expect(transport.text()).toContain("Groceries");
    expect(food.text()).not.toContain("Groceries");
  });

  it("adds a category to a group via the '+ add category' row", async () => {
    vi.spyOn(catalogApi, "listTemplates").mockResolvedValue(TEMPLATES);
    const create = vi.spyOn(catalogApi, "createCategory").mockResolvedValue({ catalog_version: 2 });
    vi.spyOn(catalogApi, "getCategories").mockResolvedValue({
      catalog_version: 2,
      categories: [
        ...VISIBLE_CATEGORIES,
        { id: 50, code: "u_snacks", name: "Snacks", group_id: 1, group_name: "Food", group_sort_order: 1, group_code: "food" },
      ],
    });
    vi.spyOn(catalogApi, "getActiveTemplate").mockResolvedValue({ active_template: "simple" });

    const { w, store } = mountSheet();
    store.activeTemplate = "simple";
    await w.find('[data-testid="manage-toggle"]').trigger("click");
    await flushPromises();

    const groups = () => w.findAll('[data-testid="manage-group"]');
    const food = () => groups().find((g) => g.find(".group-label").text() === "Food");
    await food().find(".manage-add-btn").trigger("click");

    await food().find(".manage-add-input").setValue("Snacks");
    await food().find('[aria-label="Save new category in Food"]').trigger("click");
    await flushPromises();

    expect(create).toHaveBeenCalledWith("Snacks", "food");
    expect(food().text()).toContain("Snacks");
  });
});

describe("CategorySheet — persistent bottom bar (§6)", () => {
  it("renders the set-switch and manage-toggle buttons in the footer, not the search row", () => {
    const { w } = mountSheet();
    expect(w.find(".search-wrap").find('[data-testid="manage-toggle"]').exists()).toBe(false);

    const footer = w.find(".sheet-footer");
    expect(footer.find('[data-testid="open-template-switch"]').exists()).toBe(true);
    expect(footer.find('[data-testid="manage-toggle"]').exists()).toBe(true);
  });

  it("shows the active template name and stays present while searching and in manage mode", async () => {
    localStorage.setItem("dinary:catalog:lastLang", "en");
    vi.spyOn(catalogApi, "searchCategories").mockResolvedValue([]);
    const { w, store } = mountSheet();
    store.activeTemplate = "simple";
    await flushPromises();

    expect(w.find('[data-testid="open-template-switch"]').text()).toContain("Simple");

    await search(w, "gro");
    expect(w.find('[data-testid="open-template-switch"]').exists()).toBe(true);

    await w.find(".clear-btn").trigger("click");
    await w.find('[data-testid="manage-toggle"]').trigger("click");
    expect(w.find('[data-testid="open-template-switch"]').exists()).toBe(true);
  });

  it("left button opens the shared template-switch sheet", async () => {
    const { w, store } = mountSheet();
    await w.find('[data-testid="open-template-switch"]').trigger("click");
    expect(store.templateSwitchOpen).toBe(true);
  });

  it("right icon toggles manage mode", async () => {
    const { w } = mountSheet();
    await w.find('[data-testid="manage-toggle"]').trigger("click");
    expect(w.find('[data-testid="manage-view"]').exists()).toBe(true);

    await w.find('[data-testid="manage-toggle"]').trigger("click");
    expect(w.find('[data-testid="manage-view"]').exists()).toBe(false);
  });

  it("initialManage opens the sheet straight into manage mode", () => {
    const { w } = mountSheet({ initialManage: true });
    expect(w.find('[data-testid="manage-view"]').exists()).toBe(true);
  });

  it("no inline template-switch UI remains", async () => {
    const { w } = mountSheet();
    await w.find('[data-testid="manage-toggle"]').trigger("click");
    await flushPromises();

    expect(w.find('[data-testid="switch-template-row"]').exists()).toBe(false);
    expect(w.find('[data-testid="switch-template-panel"]').exists()).toBe(false);
    expect(w.find('[data-testid="template-list"]').exists()).toBe(false);
  });
});

describe("CategorySheet — search while offline", () => {
  const SEARCH_RETRY_MS = 5000;

  it("shows the search-unavailable message when the request fails at the network level", async () => {
    const networkError = new TypeError("Failed to fetch");
    const searchSpy = vi.spyOn(catalogApi, "searchCategories").mockRejectedValue(networkError);
    const { w } = mountSheet();

    await search(w, "gro");

    expect(searchSpy).toHaveBeenCalledWith("gro");
    const offline = w.find('[data-testid="search-offline"]');
    expect(offline.exists()).toBe(true);
    expect(offline.text()).toBe("Search is unavailable offline");
    expect(w.find('[data-testid="flat-results"]').exists()).toBe(false);

    w.unmount();
  });

  it("retries automatically and clears the offline message once the search succeeds", async () => {
    vi.useFakeTimers();
    try {
      const networkError = new TypeError("Failed to fetch");
      const searchSpy = vi
        .spyOn(catalogApi, "searchCategories")
        .mockRejectedValueOnce(networkError)
        .mockResolvedValueOnce([
          { id: 10, code: "groceries", name: "Groceries", is_active: true, is_hidden: false },
        ]);
      const { w } = mountSheet();

      await w.find(".search-input").setValue("gro");
      await vi.advanceTimersByTimeAsync(SEARCH_DEBOUNCE_MS);

      expect(w.find('[data-testid="search-offline"]').exists()).toBe(true);
      expect(w.find('[data-testid="flat-results"]').exists()).toBe(false);

      await vi.advanceTimersByTimeAsync(SEARCH_RETRY_MS);

      expect(searchSpy).toHaveBeenCalledTimes(2);
      expect(w.find('[data-testid="search-offline"]').exists()).toBe(false);
      expect(w.find('[data-testid="flat-results"]').exists()).toBe(true);

      w.unmount();
    } finally {
      vi.useRealTimers();
    }
  });
});
