import { describe, it, expect, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import BreakdownPanel from "../src/components/BreakdownPanel.vue";

beforeEach(async () => {
  await allure.epic("Analytics");
  await allure.feature("Frontend");
  await allure.story("BreakdownPanel");
});

function mountPanel(props, attrs = {}) {
  return mount(BreakdownPanel, {
    props,
    attrs,
    slots: { default: '<div class="content">breakdown</div>' },
  });
}

describe("BreakdownPanel", () => {
  it("renders the slot when ready and exposes the state", () => {
    const wrapper = mountPanel({ state: "ready" });
    expect(wrapper.attributes("data-state")).toBe("ready");
    expect(wrapper.find(".content").exists()).toBe(true);
    expect(wrapper.find(".detail-skeleton").exists()).toBe(false);
  });

  it("shows a skeleton and no content while loading", () => {
    const wrapper = mountPanel({ state: "loading" });
    expect(wrapper.find(".detail-skeleton").exists()).toBe(true);
    expect(wrapper.find(".content").exists()).toBe(false);
  });

  it.each([
    ["offline", "Offline — details unavailable"],
    ["error", "Failed to load details"],
    ["empty", "No expenses yet"],
  ])("renders a single line for the %s state", (state, text) => {
    const wrapper = mountPanel({ state });
    expect(wrapper.text()).toBe(text);
    expect(wrapper.find(".detail-skeleton").exists()).toBe(false);
    expect(wrapper.find(".content").exists()).toBe(false);
  });

  it("uses the given empty text", () => {
    const wrapper = mountPanel({ state: "empty", emptyText: "Nothing here" });
    expect(wrapper.text()).toBe("Nothing here");
  });

  it("passes id and class through to the root", () => {
    const wrapper = mountPanel({ state: "ready" }, { id: "event-detail-1", class: "event-detail" });
    expect(wrapper.attributes("id")).toBe("event-detail-1");
    expect(wrapper.classes()).toContain("event-detail");
  });
});
