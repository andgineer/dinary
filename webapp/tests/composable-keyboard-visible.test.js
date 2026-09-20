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
  vi.useRealTimers();
  delete window.visualViewport;
  Object.defineProperty(window, "scrollY", { value: 0, configurable: true });
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

  it("subtracts the viewport offset and never reports a negative gap", () => {
    const vv = makeViewport(800);
    const { get } = wrapComposable();
    vv.height = 400;
    vv.offsetTop = 30;
    vv._fire("resize");
    expect(get().keyboardBottom.value).toBe(370);

    vv.height = 400.4;
    vv.offsetTop = 500;
    vv._fire("resize");
    expect(get().keyboardBottom.value).toBe(0);
  });

  it("rounds the gap to whole pixels", () => {
    const vv = makeViewport(800);
    const { get } = wrapComposable();
    vv.height = 399.6;
    vv._fire("resize");
    expect(get().keyboardBottom.value).toBe(400);
  });

  it("resets a scrolled document when the keyboard closes", () => {
    const vv = makeViewport(400);
    const scrollTo = vi.fn();
    window.scrollTo = scrollTo;
    Object.defineProperty(window, "scrollY", { value: 220, configurable: true });
    const { get } = wrapComposable();
    vv._fire("resize");
    expect(get().keyboardVisible.value).toBe(true);
    expect(scrollTo).not.toHaveBeenCalled();

    vv.height = 800;
    vv._fire("resize");
    expect(scrollTo).toHaveBeenCalledWith(0, 0);
  });

  it("re-measures after focus, when the keyboard animation has settled", () => {
    vi.useFakeTimers();
    const vv = makeViewport(800);
    const { get } = wrapComposable();
    document.dispatchEvent(new Event("focusin"));
    vv.height = 400;
    expect(get().keyboardVisible.value).toBe(false);
    vi.advanceTimersByTime(700);
    expect(get().keyboardVisible.value).toBe(true);
    expect(get().keyboardBottom.value).toBe(400);
  });

  it("removes event listeners on unmount", () => {
    const vv = makeViewport(800);
    const removeSpy = vi.spyOn(vv, "removeEventListener");
    const windowRemoveSpy = vi.spyOn(window, "removeEventListener");
    const documentRemoveSpy = vi.spyOn(document, "removeEventListener");
    const { wrapper } = wrapComposable();
    wrapper.unmount();
    expect(removeSpy).toHaveBeenCalledWith("resize", expect.any(Function));
    expect(removeSpy).toHaveBeenCalledWith("scroll", expect.any(Function));
    expect(windowRemoveSpy).toHaveBeenCalledWith("orientationchange", expect.any(Function));
    expect(documentRemoveSpy).toHaveBeenCalledWith("focusin", expect.any(Function));
    expect(documentRemoveSpy).toHaveBeenCalledWith("focusout", expect.any(Function));
  });

  it("stops pending re-measurements on unmount", () => {
    vi.useFakeTimers();
    const vv = makeViewport(800);
    const { wrapper, get } = wrapComposable();
    document.dispatchEvent(new Event("focusin"));
    wrapper.unmount();
    vv.height = 400;
    vi.advanceTimersByTime(700);
    expect(get().keyboardVisible.value).toBe(false);
  });

  it("does nothing when visualViewport is absent", () => {
    delete window.visualViewport;
    expect(() => wrapComposable()).not.toThrow();
  });
});
