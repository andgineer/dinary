import { beforeEach, describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import KeyboardSaveBar from "../src/components/KeyboardSaveBar.vue";

beforeEach(async () => {
  await allure.epic("Infrastructure");
  await allure.feature("Frontend");
  await allure.story("KeyboardSaveBar");
});

describe("KeyboardSaveBar", () => {
  it("sits at the reported keyboard height", () => {
    const wrapper = mount(KeyboardSaveBar, { props: { bottom: 336 } });
    expect(wrapper.find(".kb-save-bar").attributes("style")).toContain("bottom: 336px");
  });

  it("sits at the screen bottom when no height is given", () => {
    const wrapper = mount(KeyboardSaveBar);
    expect(wrapper.find(".kb-save-bar").attributes("style")).toContain("bottom: 0px");
  });

  it("fills the button with the view's accent color", () => {
    const wrapper = mount(KeyboardSaveBar, { props: { accentColor: "var(--success)" } });
    const style = wrapper.find(".kb-save-btn").attributes("style");
    expect(style).toContain("var(--success)");
  });

  it("leaves the button on its default fill when no accent color is given", () => {
    const wrapper = mount(KeyboardSaveBar);
    expect(wrapper.find(".kb-save-btn").attributes("style")).toBeUndefined();
  });

  it("emits save when tapped", async () => {
    const wrapper = mount(KeyboardSaveBar);
    await wrapper.find(".kb-save-btn").trigger("click");
    expect(wrapper.emitted("save")).toHaveLength(1);
  });
});
