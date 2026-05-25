import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import EditModal from "../src/modals/EditModal.vue";
import { useCatalogStore } from "../src/stores/catalog.js";

beforeEach(async () => {
  await allure.epic("Components");
  await allure.feature("EditModal");
});

// Catalog snapshot used to seed the store. EditModal reads
// catalog.tags / catalog.groups for the dependent fields.
function snapshot() {
  return {
    catalog_version: 1,
    category_groups: [
      { id: 1, name: "food", sort_order: 0, is_active: true, removable: true },
      { id: 2, name: "fun", sort_order: 1, is_active: true, removable: true },
    ],
    categories: [
      {
        id: 100,
        name: "groceries",
        group_id: 1,
        sheet_name: null,
        sheet_group: null,
        is_active: true,
        removable: true,
      },
    ],
    events: [
      {
        id: 200,
        name: "Trip",
        date_from: "2026-05-01",
        date_to: "2026-05-10",
        auto_attach_enabled: false,
        auto_tags: [],
        is_active: true,
        removable: true,
      },
    ],
    tags: [
      { id: 300, name: "biz", is_active: true, removable: true },
      { id: 301, name: "vacation", is_active: true, removable: true },
    ],
  };
}

function mountModal(props, pinia) {
  return mount(EditModal, {
    props,
    global: { plugins: [pinia] },
    attachTo: document.body,
  });
}

let pinia;
let catalog;

beforeEach(() => {
  pinia = createPinia();
  setActivePinia(pinia);
  catalog = useCatalogStore();
  catalog.replaceSnapshot(snapshot());
});

describe("EditModal — group", () => {
  it("seeds inputs from item.name and item.sort_order on open", async () => {
    const wrapper = mountModal(
      {
        open: true,
        kind: "group",
        item: { id: 1, name: "food", sort_order: 0 },
      },
      pinia,
    );
    await flushPromises();
    expect(wrapper.find("#edit-name").element.value).toBe("food");
    expect(wrapper.find("#edit-sort-order").element.value).toBe("0");
  });

  it("submits only changed fields and emits 'edited' + 'close'", async () => {
    const spy = vi.spyOn(catalog, "patch").mockResolvedValue(snapshot());
    const wrapper = mountModal(
      {
        open: true,
        kind: "group",
        item: { id: 1, name: "food", sort_order: 0 },
      },
      pinia,
    );
    await flushPromises();
    await wrapper.find("#edit-name").setValue("food-renamed");
    // Trigger the primary action (Save)
    await wrapper.findAll(".btn-primary")[0].trigger("click");
    await flushPromises();
    expect(spy).toHaveBeenCalledWith("group", 1, { name: "food-renamed" });
    expect(wrapper.emitted("edited")).toBeTruthy();
    expect(wrapper.emitted("close")).toBeTruthy();
  });

  it("closes without a network call when nothing changed", async () => {
    const spy = vi.spyOn(catalog, "patch");
    const wrapper = mountModal(
      {
        open: true,
        kind: "group",
        item: { id: 1, name: "food", sort_order: 0 },
      },
      pinia,
    );
    await flushPromises();
    await wrapper.findAll(".btn-primary")[0].trigger("click");
    await flushPromises();
    expect(spy).not.toHaveBeenCalled();
    expect(wrapper.emitted("close")).toBeTruthy();
    expect(wrapper.emitted("edited")).toBeUndefined();
  });

  it("shows an error when the name is blank", async () => {
    const wrapper = mountModal(
      {
        open: true,
        kind: "group",
        item: { id: 1, name: "food", sort_order: 0 },
      },
      pinia,
    );
    await flushPromises();
    await wrapper.find("#edit-name").setValue("   ");
    await wrapper.findAll(".btn-primary")[0].trigger("click");
    await flushPromises();
    expect(wrapper.find(".modal-error").text()).toBe("Enter a name");
  });
});

describe("EditModal — category", () => {
  it("requires a group to be selected", async () => {
    const wrapper = mountModal(
      {
        open: true,
        kind: "category",
        item: {
          id: 100,
          name: "groceries",
          group_id: 1,
          sheet_name: null,
          sheet_group: null,
        },
      },
      pinia,
    );
    await flushPromises();
    await wrapper.find("#edit-group").setValue("");
    await wrapper.findAll(".btn-primary")[0].trigger("click");
    await flushPromises();
    expect(wrapper.find(".modal-error").text()).toBe("Select a group");
  });

  it("includes group_id and sheet_* fields when changed", async () => {
    const spy = vi.spyOn(catalog, "patch").mockResolvedValue(snapshot());
    const wrapper = mountModal(
      {
        open: true,
        kind: "category",
        item: {
          id: 100,
          name: "groceries",
          group_id: 1,
          sheet_name: null,
          sheet_group: null,
        },
      },
      pinia,
    );
    await flushPromises();
    await wrapper.find("#edit-group").setValue("2");
    await wrapper.find("#edit-sheet-name").setValue("Food");
    await wrapper.findAll(".btn-primary")[0].trigger("click");
    await flushPromises();
    expect(spy).toHaveBeenCalledWith("category", 100, {
      group_id: 2,
      sheet_name: "Food",
    });
  });
});

describe("EditModal — event", () => {
  it("rejects an inverted date range", async () => {
    const wrapper = mountModal(
      {
        open: true,
        kind: "event",
        item: {
          id: 200,
          name: "Trip",
          date_from: "2026-05-01",
          date_to: "2026-05-10",
          auto_attach_enabled: false,
          auto_tags: [],
        },
      },
      pinia,
    );
    await flushPromises();
    await wrapper.find("#edit-date-from").setValue("2026-05-15");
    await wrapper.findAll(".btn-primary")[0].trigger("click");
    await flushPromises();
    expect(wrapper.find(".modal-error").text()).toBe(
      "Start date must be <= end date",
    );
  });

  it("submits only changed event fields and maps tag ids → names", async () => {
    const spy = vi.spyOn(catalog, "patch").mockResolvedValue(snapshot());
    const wrapper = mountModal(
      {
        open: true,
        kind: "event",
        item: {
          id: 200,
          name: "Trip",
          date_from: "2026-05-01",
          date_to: "2026-05-10",
          auto_attach_enabled: false,
          auto_tags: [],
        },
      },
      pinia,
    );
    await flushPromises();
    await wrapper.find("#edit-date-to").setValue("2026-05-12");
    await wrapper.find('input[type="checkbox"]').setValue(true);
    // Click the first tag chip ("biz")
    await wrapper.findAll(".tag-chip input")[0].setValue(true);
    await wrapper.findAll(".btn-primary")[0].trigger("click");
    await flushPromises();
    expect(spy).toHaveBeenCalledTimes(1);
    const [, , body] = spy.mock.calls[0];
    expect(body.date_to).toBe("2026-05-12");
    expect(body.auto_attach_enabled).toBe(true);
    expect(body.auto_tags).toEqual(["biz"]);
    expect(body.date_from).toBeUndefined();
    expect(body.name).toBeUndefined();
  });
});

describe("EditModal — tag", () => {
  it("rejects names with spaces or commas", async () => {
    const wrapper = mountModal(
      {
        open: true,
        kind: "tag",
        item: { id: 300, name: "biz" },
      },
      pinia,
    );
    await flushPromises();
    await wrapper.find("#edit-name").setValue("two words");
    await wrapper.findAll(".btn-primary")[0].trigger("click");
    await flushPromises();
    expect(wrapper.find(".modal-error").text()).toBe(
      "Tag cannot contain spaces or commas",
    );
  });

  it("submits a renamed tag", async () => {
    const spy = vi.spyOn(catalog, "patch").mockResolvedValue(snapshot());
    const wrapper = mountModal(
      {
        open: true,
        kind: "tag",
        item: { id: 300, name: "biz" },
      },
      pinia,
    );
    await flushPromises();
    await wrapper.find("#edit-name").setValue("business");
    await wrapper.findAll(".btn-primary")[0].trigger("click");
    await flushPromises();
    expect(spy).toHaveBeenCalledWith("tag", 300, { name: "business" });
  });
});

describe("EditModal — store error surfaced", () => {
  it("renders the error message and keeps the modal open", async () => {
    vi.spyOn(catalog, "patch").mockRejectedValue(new Error("nope"));
    const wrapper = mountModal(
      {
        open: true,
        kind: "tag",
        item: { id: 300, name: "biz" },
      },
      pinia,
    );
    await flushPromises();
    await wrapper.find("#edit-name").setValue("business");
    await wrapper.findAll(".btn-primary")[0].trigger("click");
    await flushPromises();
    expect(wrapper.find(".modal-error").text()).toBe("nope");
    expect(wrapper.emitted("close")).toBeUndefined();
  });
});
