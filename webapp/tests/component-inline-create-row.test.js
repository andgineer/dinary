import { beforeEach, describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import InlineCreateRow from "../src/components/InlineCreateRow.vue";

beforeEach(async () => {
  await allure.epic("Expenses");
  await allure.feature("Frontend");
  await allure.story("InlineCreateRow");
});

describe("InlineCreateRow", () => {
  it("renders an input with the given placeholder", () => {
    const wrapper = mount(InlineCreateRow, {
      props: { placeholder: "New group name…" },
    });
    expect(wrapper.find("input").element.placeholder).toBe("New group name…");
  });

  it("emits save with trimmed value on Enter", async () => {
    const wrapper = mount(InlineCreateRow, { props: {} });
    await wrapper.find("input").setValue("  my group  ");
    await wrapper.find("input").trigger("keydown.enter");
    expect(wrapper.emitted("save")).toBeTruthy();
    expect(wrapper.emitted("save")[0][0]).toBe("my group");
  });

  it("emits cancel when Esc is pressed", async () => {
    const wrapper = mount(InlineCreateRow, { props: {} });
    await wrapper.find("input").setValue("something");
    await wrapper.find("input").trigger("keydown.esc");
    expect(wrapper.emitted("cancel")).toBeTruthy();
  });

  it("emits cancel on the × button", async () => {
    const wrapper = mount(InlineCreateRow, { props: {} });
    await wrapper.find('[aria-label="Cancel"]').trigger("click");
    expect(wrapper.emitted("cancel")).toBeTruthy();
  });

  it("emits save on the ✓ button", async () => {
    const wrapper = mount(InlineCreateRow, { props: {} });
    await wrapper.find("input").setValue("hello");
    await wrapper.find('[aria-label="Confirm"]').trigger("click");
    expect(wrapper.emitted("save")).toBeTruthy();
    expect(wrapper.emitted("save")[0][0]).toBe("hello");
  });

  it("emits cancel instead of save when value is empty", async () => {
    const wrapper = mount(InlineCreateRow, { props: {} });
    await wrapper.find('[aria-label="Confirm"]').trigger("click");
    expect(wrapper.emitted("cancel")).toBeTruthy();
    expect(wrapper.emitted("save")).toBeFalsy();
  });

  it("shows validation error and blocks save when validate returns a message", async () => {
    const validate = (v) => (v.includes(" ") ? "no spaces allowed" : null);
    const wrapper = mount(InlineCreateRow, { props: { validate } });
    await wrapper.find("input").setValue("bad name");
    await wrapper.find('[aria-label="Confirm"]').trigger("click");
    expect(wrapper.emitted("save")).toBeFalsy();
    expect(wrapper.text()).toContain("no spaces allowed");
  });

  it("clears validation error and emits save on valid input", async () => {
    const validate = (v) => (v.includes(" ") ? "no spaces" : null);
    const wrapper = mount(InlineCreateRow, { props: { validate } });
    await wrapper.find("input").setValue("bad name");
    await wrapper.find('[aria-label="Confirm"]').trigger("click");
    await wrapper.find("input").setValue("goodname");
    await wrapper.find('[aria-label="Confirm"]').trigger("click");
    expect(wrapper.emitted("save")).toBeTruthy();
    expect(wrapper.find(".inline-error").exists()).toBe(false);
  });
});
