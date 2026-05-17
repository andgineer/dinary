import { ref } from "vue";

const MS_PER_DAY = 24 * 60 * 60 * 1000;

export function useStaleCache({ dirtyKey, fetchedKey, dataKey, ttlMs = MS_PER_DAY } = {}) {
  const dirtyFlag = ref(localStorage.getItem(dirtyKey) === "1");
  const lastFetchedAt = ref(Number(localStorage.getItem(fetchedKey)) || null);

  function markDirty() {
    dirtyFlag.value = true;
    localStorage.setItem(dirtyKey, "1");
  }

  function stampFresh() {
    lastFetchedAt.value = Date.now();
    localStorage.setItem(fetchedKey, String(lastFetchedAt.value));
    dirtyFlag.value = false;
    localStorage.removeItem(dirtyKey);
  }

  function bumpFetchTime() {
    lastFetchedAt.value = Date.now();
    localStorage.setItem(fetchedKey, String(lastFetchedAt.value));
  }

  function isStale() {
    const age = lastFetchedAt.value ? Date.now() - lastFetchedAt.value : Infinity;
    return dirtyFlag.value || !lastFetchedAt.value || age > ttlMs;
  }

  function readCache() {
    if (!dataKey) return null;
    try {
      const raw = localStorage.getItem(dataKey);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  }

  function writeCache(data) {
    if (!dataKey) return;
    try {
      localStorage.setItem(dataKey, JSON.stringify(data));
    } catch {}
  }

  function clearCache() {
    if (!dataKey) return;
    try {
      localStorage.removeItem(dataKey);
    } catch {}
  }

  return { dirtyFlag, lastFetchedAt, markDirty, stampFresh, bumpFetchTime, isStale, readCache, writeCache, clearCache };
}
