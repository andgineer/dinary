import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { defineComponent } from "vue";
import { useKeyboardVisible } from "../src/composables/useKeyboardVisible.js";

beforeEach(async () => {
  await allure.epic("Infrastructure");
  await allure.feature("Frontend");
  await allure.story("useKeyboardVisible");
});

function makeViewport(height, innerHeight = 800) {
  const listeners = {};
  Object.defineProperty(window, "innerHeight", { value: innerHeight, configurable: true });
  window.visualViewport = {
    height,
    offsetTop: 0,
    addEventListener: (e, fn) => { listeners[e] = fn; },
    removeEventListener: (e, fn) => { if (listeners[e] === fn) delete listeners[e]; },
    _fire: (e) => listeners[e]?.(),
  };
  return window.visualViewport;
}

function wrapComposable() {
  let result;
  const Wrapper = defineComponent({
    setup() { result = useKeyboardVisible(); return {}; },
    template: "<div/>",
  });
  const wrapper = mount(Wrapper, { attachTo: document.body });
  return { wrapper, get: () => result };
}

beforeEach(() => {
  delete window.visualViewport;
  Object.defineProperty(window, "innerHeight", { value: 800, configurable: true });
});

afterEach(() => {
  vi.restoreAllMocks();
  delete window.visualViewport;
});

describe("useKeyboardVisible", () => {
  it("starts with keyboard not visible", () => {
    makeViewport(800);
    const { get } = wrapComposable();
    expect(get().keyboardVisible.value).toBe(false);
    expect(get().keyboardBottom.value).toBe(0);
  });

  it("detects keyboard open when viewport shrinks below threshold", () => {
    const vv = makeViewport(800);
    const { get } = wrapComposable();
    vv.height = 400;
    vv._fire("resize");
    expect(get().keyboardVisible.value).toBe(true);
  });

  it("computes keyboardBottom as gap between viewport top and keyboard", () => {
    const vv = makeViewport(800);
    const { get } = wrapComposable();
    vv.height = 400;
    vv.offsetTop = 0;
    vv._fire("resize");
    // window.innerHeight(800) - offsetTop(0) - height(400) = 400
    expect(get().keyboardBottom.value).toBe(400);
  });

  it("resets to not visible when viewport restores to full height", () => {
    const vv = makeViewport(400);
    const { get } = wrapComposable();
    vv._fire("resize");
    expect(get().keyboardVisible.value).toBe(true);
    vv.height = 800;
    vv._fire("resize");
    expect(get().keyboardVisible.value).toBe(false);
    expect(get().keyboardBottom.value).toBe(0);
  });

  it("removes event listeners on unmount", () => {
    const vv = makeViewport(800);
    const removeSpy = vi.spyOn(vv, "removeEventListener");
    const { wrapper } = wrapComposable();
    wrapper.unmount();
    expect(removeSpy).toHaveBeenCalledWith("resize", expect.any(Function));
    expect(removeSpy).toHaveBeenCalledWith("scroll", expect.any(Function));
  });

  it("does nothing when visualViewport is absent", () => {
    delete window.visualViewport;
    expect(() => wrapComposable()).not.toThrow();
  });
});
