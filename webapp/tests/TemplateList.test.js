import { describe, it, expect, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import TemplateList from "../src/components/TemplateList.vue";

beforeEach(async () => {
  await allure.epic("Category templates");
  await allure.feature("Frontend");
  await allure.story("TemplateList");
});

const TEMPLATES = [
  {
    code: "simple",
    names: { ru: "Просто", en: "Simple" },
    taglines: { ru: "для начала", en: "to get started" },
    origin: "factory",
  },
  {
    code: "family",
    names: { ru: "Семья" },
    taglines: { ru: "для семейного бюджета" },
    origin: "factory",
  },
];

describe("TemplateList", () => {
  it("renders localized names and taglines for the given lang", () => {
    const w = mount(TemplateList, { props: { templates: TEMPLATES, lang: "en" } });
    const card = w.get('[data-testid="template-simple"]');
    expect(card.text()).toContain("Simple");
    expect(card.text()).toContain("to get started");
  });

  it("falls back to ru when lang is absent from a template's language set", () => {
    const w = mount(TemplateList, { props: { templates: TEMPLATES, lang: "en" } });
    const card = w.get('[data-testid="template-family"]');
    expect(card.text()).toContain("Семья");
    expect(card.text()).toContain("для семейного бюджета");
  });

  it("marks the active template", () => {
    const w = mount(TemplateList, { props: { templates: TEMPLATES, lang: "en", activeCode: "family" } });
    expect(w.get('[data-testid="template-family"]').classes()).toContain("is-active");
    expect(w.get('[data-testid="template-simple"]').classes()).not.toContain("is-active");
  });

  it("emits apply with the code on tap", async () => {
    const w = mount(TemplateList, { props: { templates: TEMPLATES, lang: "en" } });
    await w.get('[data-testid="template-simple"]').trigger("click");
    expect(w.emitted("apply")?.[0]).toEqual(["simple"]);
  });
});
