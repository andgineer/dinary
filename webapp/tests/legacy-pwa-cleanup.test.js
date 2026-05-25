import { describe, it, expect, beforeEach, vi } from "vitest";
import { runLegacyPwaCleanup } from "../src/legacy-pwa-cleanup.js";

beforeEach(async () => {
  await allure.epic("Infrastructure");
  await allure.feature("PWA");
});

const SWEEP_FLAG = "dinary:legacy-pwa-sweep-done";

function makeFakeCaches(initialNames) {
  const store = new Set(initialNames);
  return {
    keys: vi.fn(async () => Array.from(store)),
    delete: vi.fn(async (name) => store.delete(name)),
    _store: store,
  };
}

describe("legacy-pwa-cleanup", () => {
  beforeEach(() => {
    localStorage.clear();
    delete globalThis.caches;
  });

  it("deletes only legacy dinary-* caches, leaves Workbox caches", async () => {
    const fakeCaches = makeFakeCaches([
      "dinary-1.2.3",
      "dinary-abc123",
      "workbox-precache-v2-https://example/",
      "workbox-runtime",
    ]);
    globalThis.caches = fakeCaches;

    await runLegacyPwaCleanup();

    expect(fakeCaches.delete).toHaveBeenCalledWith("dinary-1.2.3");
    expect(fakeCaches.delete).toHaveBeenCalledWith("dinary-abc123");
    expect(fakeCaches.delete).not.toHaveBeenCalledWith(
      "workbox-precache-v2-https://example/",
    );
    expect(fakeCaches.delete).not.toHaveBeenCalledWith("workbox-runtime");
  });

  it("sets the persistence flag so the sweep is one-shot", async () => {
    globalThis.caches = makeFakeCaches([]);
    await runLegacyPwaCleanup();
    expect(localStorage.getItem(SWEEP_FLAG)).toBe("1");
  });

  it("is a no-op when the flag is already set", async () => {
    localStorage.setItem(SWEEP_FLAG, "1");
    const fakeCaches = makeFakeCaches(["dinary-1.0.0"]);
    globalThis.caches = fakeCaches;

    await runLegacyPwaCleanup();

    expect(fakeCaches.keys).not.toHaveBeenCalled();
  });

  it("does not throw when caches API is unavailable", async () => {
    delete globalThis.caches;
    await expect(runLegacyPwaCleanup()).resolves.toBeUndefined();
    expect(localStorage.getItem(SWEEP_FLAG)).toBe("1");
  });
});
