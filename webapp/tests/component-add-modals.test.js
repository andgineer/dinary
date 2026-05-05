import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import AddGroupModal from "../src/modals/AddGroupModal.vue";
import AddCategoryModal from "../src/modals/AddCategoryModal.vue";
import AddTagModal from "../src/modals/AddTagModal.vue";
import AddEventModal from "../src/modals/AddEventModal.vue";
import { useCatalogStore } from "../src/stores/catalog.js";
import * as catalogApi from "../src/api/catalog.js";

let pinia;

beforeEach(() => {
  pinia = createPinia();
  setActivePinia(pinia);
});

afterEach(() => {
  vi.restoreAllMocks();
});

function mountModal(Component, props = {}) {
  return mount(Component, {
    props: { open: true, ...props },
    global: { plugins: [pinia] },
  });
}

const SNAP = {
  catalog_version: 2,
  category_groups: [],
  categories: [],
  events: [],
  tags: [],
  status: "created",
};

describe("AddGroupModal", () => {
  it("calls catalog.add('group') with the trimmed name and emits 'added' + 'close'", async () => {
    const spy = vi.spyOn(catalogApi, "adminAddGroup").mockResolvedValue({
      ...SNAP,
      new_id: 7,
    });
    const wrapper = mountModal(AddGroupModal);
    await wrapper.find("#add-group-name").setValue("  food  ");
    await wrapper.findAll(".btn-primary")[0].trigger("click");
    await flushPromises();
    expect(spy).toHaveBeenCalledWith({ name: "food" });
    expect(wrapper.emitted("added")).toBeTruthy();
    expect(wrapper.emitted("close")).toBeTruthy();
  });

  it("shows an error and does not call API when the name is empty", async () => {
    const spy = vi.spyOn(catalogApi, "adminAddGroup");
    const wrapper = mountModal(AddGroupModal);
    await wrapper.findAll(".btn-primary")[0].trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Enter a name");
    expect(spy).not.toHaveBeenCalled();
  });

  it("surfaces server errors in the modal body", async () => {
    vi.spyOn(catalogApi, "adminAddGroup").mockRejectedValue(new Error("server boom"));
    const wrapper = mountModal(AddGroupModal);
    await wrapper.find("#add-group-name").setValue("food");
    await wrapper.findAll(".btn-primary")[0].trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("server boom");
    expect(wrapper.emitted("close")).toBeFalsy();
  });
});

describe("AddCategoryModal", () => {
  it("requires both a name and a groupId", async () => {
    const spy = vi.spyOn(catalogApi, "adminAddCategory");
    const wrapper = mountModal(AddCategoryModal, { groupId: null });
    await wrapper.find("#add-category-name").setValue("cafe");
    await wrapper.findAll(".btn-primary")[0].trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Select a group first");
    expect(spy).not.toHaveBeenCalled();
  });

  it("calls catalog.add('category') with name + group_id when both are present", async () => {
    const spy = vi.spyOn(catalogApi, "adminAddCategory").mockResolvedValue(SNAP);
    const wrapper = mountModal(AddCategoryModal, { groupId: 3 });
    await wrapper.find("#add-category-name").setValue("cafe");
    await wrapper.findAll(".btn-primary")[0].trigger("click");
    await flushPromises();
    expect(spy).toHaveBeenCalledWith({ name: "cafe", group_id: 3 });
  });
});

describe("AddTagModal", () => {
  it("rejects names with spaces or commas", async () => {
    const spy = vi.spyOn(catalogApi, "adminAddTag");
    const wrapper = mountModal(AddTagModal);
    await wrapper.find("#add-tag-name").setValue("two words");
    await wrapper.findAll(".btn-primary")[0].trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Tag cannot contain spaces or commas");
    expect(spy).not.toHaveBeenCalled();
  });

  it("posts a clean name", async () => {
    const spy = vi.spyOn(catalogApi, "adminAddTag").mockResolvedValue(SNAP);
    const wrapper = mountModal(AddTagModal);
    await wrapper.find("#add-tag-name").setValue("vacation");
    await wrapper.findAll(".btn-primary")[0].trigger("click");
    await flushPromises();
    expect(spy).toHaveBeenCalledWith({ name: "vacation" });
  });
});

describe("AddEventModal", () => {
  it("requires a name and accepts default dates equal to today", async () => {
    const spy = vi.spyOn(catalogApi, "adminAddEvent").mockResolvedValue(SNAP);
    const wrapper = mountModal(AddEventModal);
    // No name yet: submit should set the error.
    await wrapper.findAll(".btn-primary")[0].trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Enter a name");
    expect(spy).not.toHaveBeenCalled();

    await wrapper.find("#add-event-name").setValue("trip");
    await wrapper.findAll(".btn-primary")[0].trigger("click");
    await flushPromises();
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy.mock.calls[0][0]).toMatchObject({
      name: "trip",
      auto_attach_enabled: false,
      auto_tags: null,
    });
  });

  it("rejects when start date > end date", async () => {
    const spy = vi.spyOn(catalogApi, "adminAddEvent");
    const wrapper = mountModal(AddEventModal);
    await wrapper.find("#add-event-name").setValue("trip");
    await wrapper.find("#add-event-from").setValue("2026-05-10");
    await wrapper.find("#add-event-to").setValue("2026-05-01");
    await wrapper.findAll(".btn-primary")[0].trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Start date must be <= end date");
    expect(spy).not.toHaveBeenCalled();
  });

  it("maps selected tag ids to names in the auto_tags payload", async () => {
    const catalog = useCatalogStore();
    catalog.replaceSnapshot({
      catalog_version: 0,
      category_groups: [],
      categories: [],
      events: [],
      tags: [
        { id: 1, name: "vacation", is_active: true },
        { id: 2, name: "work", is_active: true },
      ],
    });
    const spy = vi.spyOn(catalogApi, "adminAddEvent").mockResolvedValue(SNAP);
    const wrapper = mountModal(AddEventModal);
    await wrapper.find("#add-event-name").setValue("trip");
    // Tap the first chip to select tag id=1.
    const chips = wrapper.findAll(".tag-chip");
    expect(chips.length).toBeGreaterThanOrEqual(1);
    await chips[0].find("input").trigger("change");
    await wrapper.findAll(".btn-primary")[0].trigger("click");
    await flushPromises();
    expect(spy).toHaveBeenCalled();
    expect(spy.mock.calls[0][0].auto_tags).toEqual(["vacation"]);
  });
});
