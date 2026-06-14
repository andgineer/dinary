import { describe, it, expect, beforeEach } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useToastStore } from "../src/stores/toast.js";
import { useCatalogStore } from "../src/stores/catalog.js";
import { recordOutOfSetActivation } from "../src/composables/oosNudge.js";

beforeEach(async () => {
  await allure.epic("Category templates");
  await allure.feature("Frontend");
  await allure.story("Wrong-набор nudge");
});

const STORAGE_KEY = "dinary:catalog:oosActivations";
const DAY_MS = 24 * 60 * 60 * 1000;

beforeEach(() => {
  setActivePinia(createPinia());
  localStorage.clear();
});

describe("recordOutOfSetActivation", () => {
  it("records activations without toasting below the threshold", () => {
    expect(recordOutOfSetActivation()).toBe(false);
    expect(recordOutOfSetActivation()).toBe(false);

    expect(JSON.parse(localStorage.getItem(STORAGE_KEY))).toHaveLength(2);
    expect(useToastStore().visible).toBe(false);
  });

  it("raises the persistent nudge banner and resets the counter on the 3rd activation within 30 days", () => {
    recordOutOfSetActivation();
    recordOutOfSetActivation();
    const nudged = recordOutOfSetActivation();

    expect(nudged).toBe(true);
    expect(useCatalogStore().showSetNudge).toBe(true);
    expect(localStorage.getItem("dinary:catalog:nudgeActive")).toBe("1");
    expect(useToastStore().visible).toBe(false);
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY))).toEqual([]);
  });

  it("prunes activations older than 30 days before counting", () => {
    const stale = Date.now() - 31 * DAY_MS;
    localStorage.setItem(STORAGE_KEY, JSON.stringify([stale, stale]));

    expect(recordOutOfSetActivation()).toBe(false);

    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY));
    expect(stored).toHaveLength(1);
    expect(useToastStore().visible).toBe(false);
  });
});
