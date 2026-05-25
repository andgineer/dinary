import { beforeEach, describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import CatalogSelectField from "../src/components/CatalogSelectField.vue";

beforeEach(async () => {
  await allure.epic("Components");
  await allure.feature("CatalogSelectField");
});

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

function trigger(wrapper) {
  return wrapper.find('[data-testid="catalog-trigger-group"]');
}

describe("CatalogSelectField — basics", () => {
  it("renders the trigger button with accessible label", () => {
    const wrapper = mountField();
    expect(trigger(wrapper).attributes("aria-label")).toBe("Group");
  });

  it("shows placeholder when no value selected", () => {
    const wrapper = mountField();
    expect(trigger(wrapper).text()).toContain("— select —");
  });

  it("shows the selected option label when a value is set", () => {
    const wrapper = mountField({ modelValue: "1" });
    expect(trigger(wrapper).text()).toContain("alpha");
  });

  it("respects an explicit inputId override in testid", () => {
    const wrapper = mount(CatalogSelectField, {
      props: {
        kind: "group",
        label: "Group",
        modelValue: "",
        options: ACTIVE,
        inactive: INACTIVE,
        manageOpen: false,
        pendingId: null,
        inputId: "my-id",
      },
    });
    expect(wrapper.find('[data-testid="catalog-trigger-my-id"]').exists()).toBe(true);
  });

  it("disables the trigger and swaps placeholder when selectDisabled", () => {
    const wrapper = mountField({
      selectDisabled: true,
      disabledPlaceholder: "— pick parent first —",
    });
    expect(trigger(wrapper).attributes("disabled")).toBeDefined();
    expect(trigger(wrapper).text()).toContain("— pick parent first —");
  });

  it("disables +New button when addDisabled", () => {
    const wrapper = mountField({ addDisabled: true, addTitle: "needs a parent" });
    const newBtn = wrapper.find('[aria-label="New"]');
    expect(newBtn.attributes("disabled")).toBeDefined();
  });

  it("shows the form-hint when formHint is non-empty", () => {
    const wrapper = mountField({ formHint: "Pick a group first" });
    expect(wrapper.find(".form-hint").text()).toBe("Pick a group first");
    const wrapper2 = mountField({ formHint: "" });
    expect(wrapper2.find(".form-hint").exists()).toBe(false);
  });
});

describe("CatalogSelectField — picker interaction", () => {
  it("opens picker panel on trigger click and lists options", async () => {
    const wrapper = mountField();
    await trigger(wrapper).trigger("click");
    const options = wrapper.findAll(".catalog-picker-option");
    const texts = options.map((o) => o.text().trim());
    expect(texts).toContain("alpha");
    expect(texts).toContain("beta");
  });

  it("emits update:modelValue and select-change on option click", async () => {
    const wrapper = mountField();
    await trigger(wrapper).trigger("click");
    const opts = wrapper.findAll(".catalog-picker-option");
    await opts.find((o) => o.text().includes("beta")).trigger("click");
    expect(wrapper.emitted("update:modelValue")[0]).toEqual(["2"]);
    expect(wrapper.emitted("select-change")).toBeTruthy();
  });

  it("closes the picker after selecting an option", async () => {
    const wrapper = mountField();
    await trigger(wrapper).trigger("click");
    expect(wrapper.find(".catalog-picker-panel").exists()).toBe(true);
    await wrapper.findAll(".catalog-picker-option")[0].trigger("click");
    expect(wrapper.find(".catalog-picker-panel").exists()).toBe(false);
  });

  it("shows check mark on the currently selected option", async () => {
    const wrapper = mountField({ modelValue: "1" });
    await trigger(wrapper).trigger("click");
    const selected = wrapper.find(".catalog-picker-option.is-selected");
    expect(selected.exists()).toBe(true);
    expect(selected.text()).toContain("alpha");
  });
});

describe("CatalogSelectField — icon button events", () => {
  it("emits 'add' on New icon button click and 'manage-toggle' on Manage click", async () => {
    const wrapper = mountField();
    await wrapper.find('[aria-label="New"]').trigger("click");
    await wrapper.find('[aria-label="Manage"]').trigger("click");
    expect(wrapper.emitted("add")).toBeTruthy();
    expect(wrapper.emitted("manage-toggle")).toBeTruthy();
  });

  it("shows Close label on cog button when manageOpen is true", () => {
    const open = mountField({ manageOpen: true });
    expect(open.find('[aria-label="Close"]').exists()).toBe(true);
    expect(open.find('[aria-label="Manage"]').exists()).toBe(false);
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
  it("uses optionLabelFn for the picker option text", async () => {
    const wrapper = mountField({
      optionLabelFn: (item) => `[${item.name.toUpperCase()}]`,
    });
    await trigger(wrapper).trigger("click");
    const opts = wrapper.findAll(".catalog-picker-option").map((o) => o.text().trim());
    expect(opts).toContain("[ALPHA]");
    expect(opts).toContain("[BETA]");
  });

  it("uses manageLabelFn (not optionLabelFn) for ManageList rows", () => {
    const wrapper = mountField({
      manageOpen: true,
      manageLabelFn: (item) => `${item.name}!`,
    });
    const list = wrapper.find('[data-testid="manage-list"]');
    expect(list.text()).toContain("alpha!");
  });
});
