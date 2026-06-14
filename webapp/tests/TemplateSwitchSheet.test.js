import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import TemplateSwitchSheet from "../src/components/TemplateSwitchSheet.vue";
import { useCatalogStore } from "../src/stores/catalog.js";
import { useToastStore } from "../src/stores/toast.js";
import * as catalogApi from "../src/api/catalog.js";

beforeEach(async () => {
  await allure.epic("Category templates");
  await allure.feature("Frontend");
  await allure.story("TemplateSwitchSheet");
});

const TELEPORT_STUB = { props: ["to"], template: "<div><slot /></div>" };

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

beforeEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

function mountSheet() {
  const pinia = createPinia();
  setActivePinia(pinia);
  const store = useCatalogStore(pinia);
  const w = mount(TemplateSwitchSheet, {
    global: { plugins: [pinia], stubs: { Teleport: TELEPORT_STUB } },
  });
  return { w, pinia, store };
}

describe("TemplateSwitchSheet", () => {
  it("loads the template catalog when opened", async () => {
    const listTemplates = vi.spyOn(catalogApi, "listTemplates").mockResolvedValue(TEMPLATES);
    const { w, store } = mountSheet();

    store.openTemplateSwitch();
    await flushPromises();

    expect(listTemplates).toHaveBeenCalledTimes(1);
    expect(w.find('[data-testid="template-switch-sheet"]').exists()).toBe(true);
    expect(w.findAll('[data-testid^="template-chip-"]')).toHaveLength(2);
  });

  it("apply switches the template, persists the language, and closes the sheet", async () => {
    localStorage.setItem("dinary:catalog:lastLang", "en");
    vi.spyOn(catalogApi, "listTemplates").mockResolvedValue(TEMPLATES);
    const apply = vi
      .spyOn(catalogApi, "applyTemplate")
      .mockResolvedValue({ active_template: "travel", catalog_version: 2 });
    vi.spyOn(catalogApi, "getCategories").mockResolvedValue({
      catalog_version: 2,
      categories: [],
    });
    vi.spyOn(catalogApi, "getActiveTemplate").mockResolvedValue({ active_template: "travel" });

    const { w, store } = mountSheet();
    store.openTemplateSwitch();
    await flushPromises();

    await w.find('[data-testid="template-chip-travel"]').trigger("click");
    await w.find('[data-testid="apply-template-btn"]').trigger("click");
    await flushPromises();

    expect(apply).toHaveBeenCalledWith("travel", "en");
    expect(store.templateSwitchOpen).toBe(false);
    const toast = useToastStore();
    expect(toast.message).toBe("Category set switched");
  });

  it("renders nothing when closed", async () => {
    vi.spyOn(catalogApi, "listTemplates").mockResolvedValue(TEMPLATES);
    const { w } = mountSheet();
    await flushPromises();

    expect(w.find('[data-testid="template-switch-sheet"]').exists()).toBe(false);
  });
});
