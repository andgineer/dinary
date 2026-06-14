import { describe, it, expect, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import TemplatePreviewPicker from "../src/components/TemplatePreviewPicker.vue";

beforeEach(async () => {
  await allure.epic("Category templates");
  await allure.feature("Frontend");
  await allure.story("TemplatePreviewPicker");
});

const TEMPLATES = [
  {
    code: "simple",
    names: { en: "Simple", ru: "Простой" },
    taglines: { en: "Basics only", ru: "Только основное" },
    groups: [
      {
        code: "food",
        names: { en: "Food", ru: "Еда" },
        categories: [
          { names: { en: "Groceries", ru: "Продукты" } },
          { names: { en: "Cafe", ru: "Кафе" } },
        ],
      },
      {
        code: "transport",
        names: { en: "Transport", ru: "Транспорт" },
        categories: [{ names: { en: "Taxi", ru: "Такси" } }],
      },
    ],
  },
  {
    code: "travel",
    names: { en: "Travel", ru: "Путешествия" },
    taglines: { en: "For frequent travelers", ru: "Для тех, кто часто путешествует" },
    groups: [
      {
        code: "travel_grp",
        names: { en: "Travel", ru: "Путешествия" },
        categories: [{ names: { en: "Flights", ru: "Авиабилеты" } }],
      },
    ],
  },
];

function mountPicker(props = {}) {
  return mount(TemplatePreviewPicker, {
    props: { templates: TEMPLATES, lang: "en", activeCode: null, applying: false, ...props },
  });
}

describe("TemplatePreviewPicker", () => {
  it("renders a chip for every template", () => {
    const w = mountPicker();
    const chips = w.findAll('[data-testid^="template-chip-"]');
    expect(chips.map((c) => c.text())).toEqual(["Simple", "Travel"]);
  });

  it("shows the full preview (all groups + all visible categories) for the first template", () => {
    const w = mountPicker();
    const groups = w.findAll('[data-testid="preview-group"]');
    expect(groups).toHaveLength(2);
    expect(groups[0].text()).toContain("Food");
    expect(groups[0].text()).toContain("Groceries");
    expect(groups[0].text()).toContain("Cafe");
    expect(groups[1].text()).toContain("Transport");
    expect(groups[1].text()).toContain("Taxi");
  });

  it("clicking a chip swaps the detail panel to that template's preview", async () => {
    const w = mountPicker();
    await w.find('[data-testid="template-chip-travel"]').trigger("click");

    const groups = w.findAll('[data-testid="preview-group"]');
    expect(groups).toHaveLength(1);
    expect(groups[0].text()).toContain("Travel");
    expect(groups[0].text()).toContain("Flights");
  });

  it("apply emits the selected code", async () => {
    const w = mountPicker();
    await w.find('[data-testid="template-chip-travel"]').trigger("click");
    await w.find('[data-testid="apply-template-btn"]').trigger("click");

    expect(w.emitted("apply")?.[0]).toEqual(["travel"]);
  });

  it("apply is disabled when the selected set is already active", () => {
    const w = mountPicker({ activeCode: "simple" });
    expect(w.find('[data-testid="apply-template-btn"]').attributes("disabled")).toBeDefined();
  });

  it("re-localizes the preview when lang changes", async () => {
    const w = mountPicker({ lang: "en" });
    expect(w.find('[data-testid="template-preview-panel"]').text()).toContain("Groceries");

    await w.setProps({ lang: "ru" });
    expect(w.find('[data-testid="template-preview-panel"]').text()).toContain("Продукты");
  });
});
