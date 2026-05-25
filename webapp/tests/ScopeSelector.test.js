import { beforeEach, describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import ScopeSelector from "../src/components/ScopeSelector.vue";

beforeEach(async () => {
  await allure.epic("Expenses");
  await allure.feature("Frontend");
  await allure.story("ScopeSelector");
});

const OPTIONS = [
  { value: "single", label: "Only this" },
  { value: "month", label: "Last month" },
  { value: "year", label: "This year" },
  { value: "all", label: "All history" },
];

function mountSelector(props = {}) {
  return mount(ScopeSelector, {
    props: { modelValue: "single", options: OPTIONS, ...props },
  });
}

describe("ScopeSelector — rendering", () => {
  it("renders one radio per option", () => {
    const wrapper = mountSelector();
    expect(wrapper.findAll('input[type="radio"]')).toHaveLength(4);
  });

  it("renders option labels", () => {
    const wrapper = mountSelector();
    expect(wrapper.text()).toContain("Only this");
    expect(wrapper.text()).toContain("All history");
  });

  it("marks the current modelValue as checked", () => {
    const wrapper = mountSelector({ modelValue: "all" });
    const radios = wrapper.findAll('input[type="radio"]');
    const checked = radios.filter((r) => r.element.checked);
    expect(checked).toHaveLength(1);
    expect(checked[0].element.value).toBe("all");
  });

  it("marks 'single' checked by default", () => {
    const wrapper = mountSelector();
    const singleRadio = wrapper.findAll('input[type="radio"]').find((r) => r.element.value === "single");
    expect(singleRadio.element.checked).toBe(true);
  });
});

describe("ScopeSelector — emit", () => {
  it("emits update:modelValue when a radio changes", async () => {
    const wrapper = mountSelector({ modelValue: "single" });
    const allRadio = wrapper.findAll('input[type="radio"]').find((r) => r.element.value === "all");
    await allRadio.setValue("all");
    expect(wrapper.emitted("update:modelValue")).toBeTruthy();
    expect(wrapper.emitted("update:modelValue")[0][0]).toBe("all");
  });
});
