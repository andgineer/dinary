import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useToastStore } from "../src/stores/toast.js";

beforeEach(async () => {
  await allure.epic("Infrastructure");
  await allure.feature("Frontend");
  await allure.story("Toast store");
});

beforeEach(() => {
  setActivePinia(createPinia());
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("toast store", () => {
  it("show() makes the toast visible with the given type and message", () => {
    const t = useToastStore();
    t.show("hello", "success");
    expect(t.visible).toBe(true);
    expect(t.message).toBe("hello");
    expect(t.type).toBe("success");
  });

  it("auto-hides after 3000ms for short messages, 8000ms for long", () => {
    const t = useToastStore();
    t.show("short", "info");
    vi.advanceTimersByTime(3000);
    expect(t.visible).toBe(false);

    t.show("a".repeat(80), "info");
    vi.advanceTimersByTime(3000);
    expect(t.visible).toBe(true);
    vi.advanceTimersByTime(5000);
    expect(t.visible).toBe(false);
  });

  it("show() while another toast is visible replaces it and resets the timer", () => {
    const t = useToastStore();
    t.show("first", "info");
    vi.advanceTimersByTime(2000);
    t.show("second", "error");
    expect(t.message).toBe("second");
    expect(t.type).toBe("error");
    vi.advanceTimersByTime(2999);
    expect(t.visible).toBe(true);
    vi.advanceTimersByTime(1);
    expect(t.visible).toBe(false);
  });

  it("hide() cancels the timer and clears visibility", () => {
    const t = useToastStore();
    t.show("x", "info");
    t.hide();
    expect(t.visible).toBe(false);
  });
});
