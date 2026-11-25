import { describe, it, expect, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import BreakdownRow from "../src/components/BreakdownRow.vue";

beforeEach(async () => {
  await allure.epic("Analytics");
  await allure.feature("Frontend");
  await allure.story("BreakdownRow");
});

describe("BreakdownRow", () => {
  it("renders label, sublabel, amount and currency", () => {
    const wrapper = mount(BreakdownRow, {
      props: { label: "food", sublabel: "Food", amount: "300", currency: "RSD", width: 1 },
    });
    expect(wrapper.find(".detail-name").text()).toContain("food");
    expect(wrapper.find(".detail-group").text()).toBe("Food");
    expect(wrapper.find(".detail-amount").text()).toBe("300 RSD");
  });

  it("omits the sublabel, currency and detail line when not given", () => {
    const wrapper = mount(BreakdownRow, { props: { label: "food", amount: "+50" } });
    expect(wrapper.find(".detail-group").exists()).toBe(false);
    expect(wrapper.find(".detail-currency").exists()).toBe(false);
    expect(wrapper.find(".detail-sub").exists()).toBe(false);
  });

  it("renders no bar without a width", () => {
    const wrapper = mount(BreakdownRow, { props: { label: "3 May", amount: "150", currency: "RSD" } });
    expect(wrapper.find(".detail-bar-track").exists()).toBe(false);
  });

  it("renders the detail line", () => {
    const wrapper = mount(BreakdownRow, { props: { label: "food", amount: "+50", detail: "100 → 150" } });
    expect(wrapper.find(".detail-sub").text()).toBe("100 → 150");
  });

  it.each([
    [1, "100%"],
    [0.5, "50%"],
    [0, "0%"],
    [1.7, "100%"],
    [-0.2, "0%"],
  ])("sizes the bar from width %s", (width, expected) => {
    const wrapper = mount(BreakdownRow, { props: { label: "x", amount: "1", width } });
    expect(wrapper.find(".detail-bar").attributes("style")).toContain(`width: ${expected}`);
  });

  it.each([
    ["up", "var(--up)"],
    ["down", "var(--down)"],
  ])("colours amount and bar for direction %s", (direction, colour) => {
    const wrapper = mount(BreakdownRow, { props: { label: "x", amount: "+1", width: 1, direction } });
    expect(wrapper.find(".detail-amount").attributes("style")).toContain(`color: ${colour}`);
    expect(wrapper.find(".detail-bar").attributes("style")).toContain(`background: ${colour}`);
  });

  it("keeps the neutral colour without a direction", () => {
    const wrapper = mount(BreakdownRow, { props: { label: "x", amount: "1", width: 1 } });
    expect(wrapper.find(".detail-amount").attributes("style")).toBeUndefined();
    expect(wrapper.find(".detail-bar").attributes("style")).not.toContain("background");
  });
});
