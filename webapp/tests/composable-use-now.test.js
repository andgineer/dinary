import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { defineComponent, h } from "vue";
import { useNow } from "../src/composables/useNow.js";

beforeEach(async () => {
  await allure.epic("Infrastructure");
  await allure.feature("Frontend");
  await allure.story("useNow");
});

let captured = null;

const TestComp = defineComponent({
  setup() {
    captured = useNow(1000);
    return () => h("span", String(captured.value));
  },
});

beforeEach(() => {
  captured = null;
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("useNow", () => {
  it("advances the ref on every tick", async () => {
    const wrapper = mount(TestComp);
    const first = captured.value;

    vi.advanceTimersByTime(1000);
    await wrapper.vm.$nextTick();
    expect(captured.value).toBe(first + 1000);

    vi.advanceTimersByTime(2000);
    await wrapper.vm.$nextTick();
    expect(captured.value).toBe(first + 3000);

    wrapper.unmount();
  });

  it("renders the current value", async () => {
    const wrapper = mount(TestComp);
    expect(wrapper.text()).toBe(String(captured.value));

    vi.advanceTimersByTime(1000);
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toBe(String(captured.value));

    wrapper.unmount();
  });

  it("clears the interval on unmount", () => {
    const wrapper = mount(TestComp);
    const ref = captured;
    wrapper.unmount();

    const frozen = ref.value;
    vi.advanceTimersByTime(10_000);
    expect(ref.value).toBe(frozen);
  });
});
