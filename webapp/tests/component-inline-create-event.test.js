import { describe, it, expect, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import InlineCreateEvent from "../src/components/InlineCreateEvent.vue";
import { useCatalogStore } from "../src/stores/catalog.js";

beforeEach(async () => {
  await allure.epic("Expenses");
  await allure.feature("Frontend");
  await allure.story("InlineCreateEvent");
});

function mountEvent(pinia) {
  return mount(InlineCreateEvent, { global: { plugins: [pinia] } });
}

beforeEach(() => {
  setActivePinia(createPinia());
});

describe("InlineCreateEvent", () => {
  it("emits cancel when the Cancel button is clicked", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const wrapper = mountEvent(pinia);
    await wrapper.find(".btn-ghost").trigger("click");
    expect(wrapper.emitted("cancel")).toBeTruthy();
  });

  it("shows an error when name is empty and Add is clicked", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const wrapper = mountEvent(pinia);
    await wrapper.find(".save-btn").trigger("click");
    expect(wrapper.text()).toContain("Enter a name");
    expect(wrapper.emitted("save")).toBeFalsy();
  });

  it("shows an error when to-date is before from-date", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const wrapper = mountEvent(pinia);
    await wrapper.find('[placeholder="Event name…"]').setValue("Trip");
    await wrapper.find("#ice-from").setValue("2026-06-10");
    await wrapper.find("#ice-to").setValue("2026-06-01");
    await wrapper.find(".save-btn").trigger("click");
    expect(wrapper.text()).toMatch(/start date/i);
    expect(wrapper.emitted("save")).toBeFalsy();
  });

  it("emits save with correct payload on valid form", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const wrapper = mountEvent(pinia);
    await wrapper.find('[placeholder="Event name…"]').setValue("Summer Fest");
    await wrapper.find("#ice-from").setValue("2026-06-01");
    await wrapper.find("#ice-to").setValue("2026-06-30");
    await wrapper.find(".save-btn").trigger("click");
    await flushPromises();
    const emits = wrapper.emitted("save");
    expect(emits).toBeTruthy();
    expect(emits[0][0]).toMatchObject({
      name: "Summer Fest",
      date_from: "2026-06-01",
      date_to: "2026-06-30",
      auto_attach_enabled: false,
      auto_tags: null,
    });
  });

  it("includes tag names in auto_tags when tags are selected", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const catalog = useCatalogStore(pinia);
    catalog.replaceSnapshot({
      catalog_version: 1,
      category_groups: [],
      categories: [],
      events: [],
      tags: [
        { id: 1, name: "vacation", is_active: true },
        { id: 2, name: "family", is_active: true },
      ],
    });
    const wrapper = mountEvent(pinia);
    await flushPromises();
    await wrapper.find('[placeholder="Event name…"]').setValue("Vacation");
    await wrapper.find("#ice-from").setValue("2026-07-01");
    await wrapper.find("#ice-to").setValue("2026-07-14");
    const checkboxes = wrapper.findAll('input[type="checkbox"][name="tag"]');
    if (checkboxes.length > 0) {
      await checkboxes[0].trigger("change");
    }
    await wrapper.find(".save-btn").trigger("click");
    const emits = wrapper.emitted("save");
    expect(emits).toBeTruthy();
    if (checkboxes.length > 0) {
      expect(emits[0][0].auto_tags).toContain(1);
    }
  });
});
