import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { useStaleCache } from "../src/composables/useStaleCache.js";

const DIRTY_KEY = "test:dirty";
const FETCHED_KEY = "test:fetchedAt";
const DATA_KEY = "test:data";
const TTL_MS = 1000;

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

describe("useStaleCache: markDirty()", () => {
  it("sets dirtyFlag to true", () => {
    const { dirtyFlag, markDirty } = useStaleCache({ dirtyKey: DIRTY_KEY, fetchedKey: FETCHED_KEY });
    expect(dirtyFlag.value).toBe(false);
    markDirty();
    expect(dirtyFlag.value).toBe(true);
  });

  it("persists dirty flag to localStorage", () => {
    const { markDirty } = useStaleCache({ dirtyKey: DIRTY_KEY, fetchedKey: FETCHED_KEY });
    markDirty();
    expect(localStorage.getItem(DIRTY_KEY)).toBe("1");
  });
});

describe("useStaleCache: stampFresh()", () => {
  it("clears dirtyFlag", () => {
    const { dirtyFlag, markDirty, stampFresh } = useStaleCache({
      dirtyKey: DIRTY_KEY,
      fetchedKey: FETCHED_KEY,
    });
    markDirty();
    stampFresh();
    expect(dirtyFlag.value).toBe(false);
  });

  it("removes dirty key from localStorage", () => {
    const { markDirty, stampFresh } = useStaleCache({ dirtyKey: DIRTY_KEY, fetchedKey: FETCHED_KEY });
    markDirty();
    stampFresh();
    expect(localStorage.getItem(DIRTY_KEY)).toBeNull();
  });

  it("sets lastFetchedAt to a recent timestamp", () => {
    const { lastFetchedAt, stampFresh } = useStaleCache({
      dirtyKey: DIRTY_KEY,
      fetchedKey: FETCHED_KEY,
    });
    const before = Date.now();
    stampFresh();
    expect(lastFetchedAt.value).toBeGreaterThanOrEqual(before);
  });

  it("persists timestamp to localStorage", () => {
    const { stampFresh } = useStaleCache({ dirtyKey: DIRTY_KEY, fetchedKey: FETCHED_KEY });
    stampFresh();
    expect(Number(localStorage.getItem(FETCHED_KEY))).toBeGreaterThan(0);
  });
});

describe("useStaleCache: bumpFetchTime()", () => {
  it("updates lastFetchedAt without clearing dirtyFlag", () => {
    const { dirtyFlag, markDirty, bumpFetchTime, isStale } = useStaleCache({
      dirtyKey: DIRTY_KEY,
      fetchedKey: FETCHED_KEY,
      ttlMs: TTL_MS,
    });
    markDirty();
    const before = Date.now();
    bumpFetchTime();
    expect(dirtyFlag.value).toBe(true);
    expect(Number(localStorage.getItem(FETCHED_KEY))).toBeGreaterThanOrEqual(before);
    expect(isStale()).toBe(true);
  });
});

describe("useStaleCache: isStale()", () => {
  it("returns true when dirty flag is set", () => {
    const { markDirty, isStale } = useStaleCache({
      dirtyKey: DIRTY_KEY,
      fetchedKey: FETCHED_KEY,
      ttlMs: TTL_MS,
    });
    markDirty();
    expect(isStale()).toBe(true);
  });

  it("returns false when clean and data is recent", () => {
    const { lastFetchedAt, isStale } = useStaleCache({
      dirtyKey: DIRTY_KEY,
      fetchedKey: FETCHED_KEY,
      ttlMs: TTL_MS,
    });
    lastFetchedAt.value = Date.now();
    expect(isStale()).toBe(false);
  });

  it("returns true when clean but data is older than TTL", () => {
    const { lastFetchedAt, isStale } = useStaleCache({
      dirtyKey: DIRTY_KEY,
      fetchedKey: FETCHED_KEY,
      ttlMs: TTL_MS,
    });
    lastFetchedAt.value = Date.now() - TTL_MS - 1;
    expect(isStale()).toBe(true);
  });

  it("returns true when never fetched", () => {
    const { isStale } = useStaleCache({
      dirtyKey: DIRTY_KEY,
      fetchedKey: FETCHED_KEY,
      ttlMs: TTL_MS,
    });
    expect(isStale()).toBe(true);
  });
});

describe("useStaleCache: readCache / writeCache / clearCache", () => {
  it("readCache returns null when no dataKey provided", () => {
    const { readCache } = useStaleCache({ dirtyKey: DIRTY_KEY, fetchedKey: FETCHED_KEY });
    expect(readCache()).toBeNull();
  });

  it("readCache returns null when nothing stored", () => {
    const { readCache } = useStaleCache({ dirtyKey: DIRTY_KEY, fetchedKey: FETCHED_KEY, dataKey: DATA_KEY });
    expect(readCache()).toBeNull();
  });

  it("writeCache stores data and readCache retrieves it", () => {
    const { readCache, writeCache } = useStaleCache({ dirtyKey: DIRTY_KEY, fetchedKey: FETCHED_KEY, dataKey: DATA_KEY });
    writeCache({ items: [1, 2, 3], count: 3 });
    expect(readCache()).toEqual({ items: [1, 2, 3], count: 3 });
  });

  it("clearCache removes stored data", () => {
    const { readCache, writeCache, clearCache } = useStaleCache({ dirtyKey: DIRTY_KEY, fetchedKey: FETCHED_KEY, dataKey: DATA_KEY });
    writeCache({ items: [1] });
    clearCache();
    expect(readCache()).toBeNull();
  });

  it("writeCache is a no-op when no dataKey provided", () => {
    const { writeCache } = useStaleCache({ dirtyKey: DIRTY_KEY, fetchedKey: FETCHED_KEY });
    expect(() => writeCache({ x: 1 })).not.toThrow();
    expect(localStorage.getItem(DATA_KEY)).toBeNull();
  });

  it("readCache returns null on malformed JSON", () => {
    localStorage.setItem(DATA_KEY, "not-json{{{");
    const { readCache } = useStaleCache({ dirtyKey: DIRTY_KEY, fetchedKey: FETCHED_KEY, dataKey: DATA_KEY });
    expect(readCache()).toBeNull();
  });
});
