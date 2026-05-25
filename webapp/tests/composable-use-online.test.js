import { beforeEach, describe, it, expect, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { defineComponent } from "vue";
import { useOnline } from "../src/composables/useOnline.js";

beforeEach(async () => {
  await allure.epic("Infrastructure");
  await allure.feature("Frontend");
  await allure.story("useOnline");
});

function mockOnLine(value) {
  const ownBefore = Object.getOwnPropertyDescriptor(navigator, "onLine");
  Object.defineProperty(navigator, "onLine", { configurable: true, get: () => value });
  return () => {
    if (ownBefore) {
      Object.defineProperty(navigator, "onLine", ownBefore);
    } else {
      delete navigator.onLine;
    }
  };
}

const TestComp = defineComponent({
  setup() { return useOnline(); },
  template: `<span>{{ isOnline }}</span>`,
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useOnline", () => {
  it("reflects navigator.onLine on mount when true", async () => {
    const restore = mockOnLine(true);
    try {
      const wrapper = mount(TestComp);
      await flushPromises();
      expect(wrapper.find("span").text()).toBe("true");
      wrapper.unmount();
    } finally {
      restore();
    }
  });

  it("reflects navigator.onLine on mount when false", async () => {
    const restore = mockOnLine(false);
    try {
      const wrapper = mount(TestComp);
      await flushPromises();
      expect(wrapper.find("span").text()).toBe("false");
      wrapper.unmount();
    } finally {
      restore();
    }
  });

  it("updates to false when offline event fires", async () => {
    const restore = mockOnLine(true);
    try {
      const wrapper = mount(TestComp);
      await flushPromises();
      expect(wrapper.find("span").text()).toBe("true");
      window.dispatchEvent(new Event("offline"));
      await flushPromises();
      expect(wrapper.find("span").text()).toBe("false");
      wrapper.unmount();
    } finally {
      restore();
    }
  });

  it("updates to true when online event fires", async () => {
    const restore = mockOnLine(false);
    try {
      const wrapper = mount(TestComp);
      await flushPromises();
      expect(wrapper.find("span").text()).toBe("false");
      window.dispatchEvent(new Event("online"));
      await flushPromises();
      expect(wrapper.find("span").text()).toBe("true");
      wrapper.unmount();
    } finally {
      restore();
    }
  });

  it("removes event listeners on unmount", async () => {
    const removeSpy = vi.spyOn(window, "removeEventListener");
    const restore = mockOnLine(true);
    try {
      const wrapper = mount(TestComp);
      await flushPromises();
      wrapper.unmount();
      expect(removeSpy).toHaveBeenCalledWith("online", expect.any(Function));
      expect(removeSpy).toHaveBeenCalledWith("offline", expect.any(Function));
    } finally {
      restore();
    }
  });
});
