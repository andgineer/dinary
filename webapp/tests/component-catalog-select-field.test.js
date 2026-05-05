import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import CatalogSelectField from "../src/components/CatalogSelectField.vue";

const ACTIVE = [
  { id: 1, name: "alpha" },
  { id: 2, name: "beta" },
];
const INACTIVE = [{ id: 9, name: "old", removable: true }];

function mountField(props = {}) {
  return mount(CatalogSelectField, {
    props: {
      kind: "group",
      label: "Group",
      modelValue: "",
      options: ACTIVE,
      inactive: INACTIVE,
      manageOpen: false,
      pendingId: null,
      ...props,
    },
  });
}

describe("CatalogSelectField — basics", () => {
  it("renders the label and the dropdown options under id matching kind", () => {
    const wrapper = mountField();
    expect(wrapper.find("label").text()).toContain("Group");
    expect(wrapper.find("select#group").exists()).toBe(true);
    const opts = wrapper.findAll("select#group option").map((o) => o.text().trim());
    expect(opts).toEqual(["— select —", "alpha", "beta"]);
  });

  it("respects an explicit inputId override", () => {
    const wrapper = mountField({ inputId: "my-id" });
    expect(wrapper.find("select#my-id").exists()).toBe(true);
    expect(wrapper.find("select#group").exists()).toBe(false);
  });

  it("disables the select and swaps the placeholder when selectDisabled", () => {
    const wrapper = mountField({
      selectDisabled: true,
      disabledPlaceholder: "— pick parent first —",
    });
    const sel = wrapper.find("select");
    expect(sel.attributes("disabled")).toBeDefined();
    expect(sel.find("option").text().trim()).toBe("— pick parent first —");
  });

  it("disables the +New button and surfaces its title when addDisabled", () => {
    const wrapper = mountField({ addDisabled: true, addTitle: "needs a parent" });
    const newBtn = wrapper.findAll("button").find((b) => b.text() === "+ New");
    expect(newBtn.attributes("disabled")).toBeDefined();
    expect(newBtn.attributes("title")).toBe("needs a parent");
  });

  it("shows the form-hint slot only when formHint is non-empty", () => {
    const wrapper = mountField({ formHint: "Pick a group first" });
    expect(wrapper.find(".form-hint").text()).toBe("Pick a group first");
    const wrapper2 = mountField({ formHint: "" });
    expect(wrapper2.find(".form-hint").exists()).toBe(false);
  });
});

describe("CatalogSelectField — events", () => {
  it("emits update:modelValue and select-change on <select> change", async () => {
    const wrapper = mountField();
    await wrapper.find("select").setValue("2");
    expect(wrapper.emitted("update:modelValue")[0]).toEqual(["2"]);
    expect(wrapper.emitted("select-change")).toBeTruthy();
  });

  it("emits 'add' on +New click and 'manage-toggle' on Manage click", async () => {
    const wrapper = mountField();
    const buttons = wrapper.findAll("button");
    await buttons.find((b) => b.text() === "+ New").trigger("click");
    await buttons.find((b) => b.text() === "Manage").trigger("click");
    expect(wrapper.emitted("add")).toBeTruthy();
    expect(wrapper.emitted("manage-toggle")).toBeTruthy();
  });

  it("toggles the Manage button label between Manage / Close", () => {
    const closed = mountField({ manageOpen: false });
    const open = mountField({ manageOpen: true });
    const closedLabels = closed.findAll("button").map((b) => b.text());
    const openLabels = open.findAll("button").map((b) => b.text());
    expect(closedLabels).toContain("Manage");
    expect(closedLabels).not.toContain("Close");
    expect(openLabels).toContain("Close");
    expect(openLabels).not.toContain("Manage");
  });
});

describe("CatalogSelectField — ManageList wiring", () => {
  it("does not render the ManageList when manageOpen is false", () => {
    const wrapper = mountField({ manageOpen: false });
    expect(wrapper.find('[data-testid="manage-list"]').exists()).toBe(false);
  });

  it("renders the ManageList with active+inactive when manageOpen is true", () => {
    const wrapper = mountField({ manageOpen: true });
    const list = wrapper.find('[data-testid="manage-list"]');
    expect(list.exists()).toBe(true);
    expect(list.text()).toContain("alpha");
    expect(list.text()).toContain("old");
  });

  it("forwards deactivate / reactivate / delete / edit from the ManageList", async () => {
    const wrapper = mountField({ manageOpen: true });
    const list = wrapper.findComponent({ name: "ManageList" });
    list.vm.$emit("deactivate", { id: 1 });
    list.vm.$emit("reactivate", { id: 9 });
    list.vm.$emit("delete", { id: 9 });
    list.vm.$emit("edit", { id: 1 });
    expect(wrapper.emitted("deactivate")[0]).toEqual([{ id: 1 }]);
    expect(wrapper.emitted("reactivate")[0]).toEqual([{ id: 9 }]);
    expect(wrapper.emitted("delete")[0]).toEqual([{ id: 9 }]);
    expect(wrapper.emitted("edit")[0]).toEqual([{ id: 1 }]);
  });
});

describe("CatalogSelectField — label formatters", () => {
  it("uses optionLabelFn for the dropdown text", () => {
    const wrapper = mountField({
      optionLabelFn: (item) => `[${item.name.toUpperCase()}]`,
    });
    const opts = wrapper.findAll("option").map((o) => o.text().trim()).slice(1);
    expect(opts).toEqual(["[ALPHA]", "[BETA]"]);
  });

  it("uses manageLabelFn (not optionLabelFn) for ManageList rows", () => {
    const wrapper = mountField({
      manageOpen: true,
      manageLabelFn: (item) => `${item.name}!`,
    });
    const list = wrapper.find('[data-testid="manage-list"]');
    // active alpha → "alpha!"; <select> options stay on default name
    expect(list.text()).toContain("alpha!");
    const dropdownOpts = wrapper.findAll("select option").map((o) => o.text().trim());
    expect(dropdownOpts).toEqual(["— select —", "alpha", "beta"]);
  });
});
