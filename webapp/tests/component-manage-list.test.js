import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mount } from "@vue/test-utils";
import ManageList from "../src/components/ManageList.vue";

beforeEach(async () => {
  await allure.epic("Infrastructure");
  await allure.feature("Frontend");
  await allure.story("ManageList");
});

const ACTIVE = [
  { id: 10, name: "food", removable: true },
  { id: 11, name: "trips", removable: false },
];
const INACTIVE = [
  { id: 20, name: "old", removable: true },
  { id: 21, name: "stuck", removable: false },
];

let originalConfirm;

beforeEach(() => {
  originalConfirm = window.confirm;
});

afterEach(() => {
  window.confirm = originalConfirm;
});

describe("ManageList", () => {
  it("renders Active and Inactive section dividers", () => {
    const wrapper = mount(ManageList, {
      props: { kind: "group", active: ACTIVE, inactive: INACTIVE },
    });
    expect(wrapper.find('[aria-label="Active"]').exists()).toBe(true);
    expect(wrapper.find('[aria-label="Inactive"]').exists()).toBe(true);
  });

  it("active rows show Hide; Delete only when removable", () => {
    const wrapper = mount(ManageList, {
      props: { kind: "group", active: ACTIVE, inactive: [] },
    });
    expect(wrapper.findAll(".inactive-hide")).toHaveLength(2);
    expect(wrapper.findAll(".inactive-delete")).toHaveLength(1);
  });

  it("inactive rows show Restore; Delete only when removable", () => {
    const wrapper = mount(ManageList, {
      props: { kind: "tag", active: [], inactive: INACTIVE },
    });
    expect(wrapper.findAll(".inactive-activate")).toHaveLength(2);
    expect(wrapper.findAll(".inactive-delete")).toHaveLength(1);
  });

  it("emits 'deactivate' with the item when Hide is clicked", async () => {
    const wrapper = mount(ManageList, {
      props: { kind: "group", active: ACTIVE, inactive: [] },
    });
    await wrapper.findAll(".inactive-hide")[0].trigger("click");
    const emitted = wrapper.emitted("deactivate");
    expect(emitted).toBeTruthy();
    expect(emitted[0][0]).toEqual(ACTIVE[0]);
  });

  it("emits 'reactivate' when Restore is clicked", async () => {
    const wrapper = mount(ManageList, {
      props: { kind: "tag", active: [], inactive: INACTIVE },
    });
    await wrapper.findAll(".inactive-activate")[0].trigger("click");
    expect(wrapper.emitted("reactivate")[0][0]).toEqual(INACTIVE[0]);
  });

  it("emits 'delete' only after confirm() resolves true", async () => {
    window.confirm = vi.fn(() => true);
    const wrapper = mount(ManageList, {
      props: { kind: "category", active: ACTIVE, inactive: INACTIVE },
    });
    await wrapper.findAll(".inactive-delete")[0].trigger("click");
    expect(window.confirm).toHaveBeenCalled();
    expect(wrapper.emitted("delete")[0][0].id).toBe(10);
  });

  it("does not emit 'delete' when confirm() returns false", async () => {
    window.confirm = vi.fn(() => false);
    const wrapper = mount(ManageList, {
      props: { kind: "category", active: ACTIVE, inactive: [] },
    });
    await wrapper.findAll(".inactive-delete")[0].trigger("click");
    expect(wrapper.emitted("delete")).toBeUndefined();
  });

  it("disables all buttons for the row whose id matches pendingId", () => {
    const wrapper = mount(ManageList, {
      props: {
        kind: "group",
        active: ACTIVE,
        inactive: [],
        pendingId: 10,
      },
    });
    const hide = wrapper.findAll(".inactive-hide")[0];
    const del = wrapper.findAll(".inactive-delete")[0];
    expect(hide.attributes("disabled")).toBeDefined();
    expect(del.attributes("disabled")).toBeDefined();
    const otherHide = wrapper.findAll(".inactive-hide")[1];
    expect(otherHide.attributes("disabled")).toBeUndefined();
  });

  it("shows Edit on every active row and emits 'edit' with the item", async () => {
    const wrapper = mount(ManageList, {
      props: { kind: "group", active: ACTIVE, inactive: INACTIVE },
    });
    // Edit appears on each active row regardless of `removable`, but
    // never on inactive rows (you reactivate first to edit).
    const editBtns = wrapper.findAll(".inactive-edit");
    expect(editBtns).toHaveLength(2);
    await editBtns[1].trigger("click");
    expect(wrapper.emitted("edit")[0][0]).toEqual(ACTIVE[1]);
  });

  it("disables Edit when pendingId matches", () => {
    const wrapper = mount(ManageList, {
      props: {
        kind: "group",
        active: ACTIVE,
        inactive: [],
        pendingId: 10,
      },
    });
    const edit = wrapper.findAll(".inactive-edit")[0];
    expect(edit.attributes("disabled")).toBeDefined();
  });
});
