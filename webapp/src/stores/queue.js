import { defineStore } from "pinia";
import { ref } from "vue";

const DB_NAME = "dinary-v2";
const DB_VERSION = 1;
const STORE_NAME = "pending_expenses";

// Errors thrown by IndexedDB when the cached connection has been closed
// out from under us (browser low-memory eviction, "Database is
// disconnecting" race). We catch these names and reopen the DB on the
// next call instead of bubbling the failure up to the form.
const DB_DISCONNECT_ERRORS = new Set([
  "InvalidStateError",
  "TransactionInactiveError",
  "AbortError",
  "UnknownError",
]);

const DB_DISCONNECT_MESSAGES = [
  /database is disconnecting/i,
  /the database connection is closing/i,
  /connection.*closed/i,
];

export function isDisconnectError(err) {
  if (!err) return false;
  if (err.name && DB_DISCONNECT_ERRORS.has(err.name)) return true;
  return DB_DISCONNECT_MESSAGES.some((re) => re.test(String(err.message ?? "")));
}

let _dbPromise = null;

function openDb() {
  if (_dbPromise) return _dbPromise;
  _dbPromise = new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME, { keyPath: "id", autoIncrement: true });
      }
    };
    req.onsuccess = () => {
      const db = req.result;
      // Bug #4 mitigation: if the browser closes the connection after
      // we cached it, drop the promise so the next caller reopens.
      db.onclose = () => {
        if (_dbPromise === thisPromise) _dbPromise = null;
      };
      db.onversionchange = () => {
        // Another tab requested an upgrade; close so it can proceed.
        db.close();
        if (_dbPromise === thisPromise) _dbPromise = null;
      };
      resolve(db);
    };
    req.onerror = () => {
      _dbPromise = null;
      reject(req.error);
    };
    req.onblocked = () => {
      // Treat as transient — caller will retry.
      _dbPromise = null;
      reject(new Error("indexedDB open blocked"));
    };
  });
  const thisPromise = _dbPromise;
  return _dbPromise;
}

function resetDb() {
  _dbPromise = null;
}

async function withDb(fn) {
  // Open + run + on disconnect-style errors, drop the cached promise and
  // retry once. This covers the legacy "Database is disconnecting" race
  // (Bug #4) without surfacing the failure to the caller.
  try {
    const db = await openDb();
    return await fn(db);
  } catch (err) {
    if (isDisconnectError(err)) {
      resetDb();
      const db = await openDb();
      return fn(db);
    }
    throw err;
  }
}

function runTx(db, mode, work) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, mode);
    let result;
    try {
      result = work(tx.objectStore(STORE_NAME), tx);
    } catch (e) {
      reject(e);
      return;
    }
    tx.oncomplete = () => resolve(result);
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error || new Error("transaction aborted"));
  });
}

function reqToPromise(req) {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

export async function _resetForTest() {
  if (_dbPromise) {
    try {
      const db = await _dbPromise;
      db.close();
    } catch {
      // intentionally swallowed
    }
  }
  resetDb();
}

export const useQueueStore = defineStore("queue", () => {
  const items = ref([]);
  const lastFlushError = ref(null);

  async function refresh() {
    const all = await withDb(async (db) =>
      runTx(db, "readonly", (store) => reqToPromise(store.getAll())),
    );
    items.value = all ?? [];
    return items.value;
  }

  async function enqueue(expense) {
    // Idempotency key stamped at enqueue time so a single user action
    // maps to one client_expense_id even across flush retries / restarts.
    const clientExpenseId =
      expense.client_expense_id ||
      (typeof crypto !== "undefined" && crypto.randomUUID
        ? crypto.randomUUID()
        : `cid-${Date.now()}-${Math.random().toString(16).slice(2)}`);
    await withDb(async (db) =>
      runTx(db, "readwrite", (store) =>
        store.add({
          ...expense,
          client_expense_id: clientExpenseId,
          queued_at: Date.now(),
        }),
      ),
    );
    await refresh();
  }

  async function remove(id) {
    await withDb(async (db) =>
      runTx(db, "readwrite", (store) => store.delete(id)),
    );
    await refresh();
  }

  async function update(item) {
    await withDb(async (db) =>
      runTx(db, "readwrite", (store) => store.put(item)),
    );
    await refresh();
  }

  async function count() {
    const all = await withDb(async (db) =>
      runTx(db, "readonly", (store) => reqToPromise(store.getAll())),
    );
    return all.length;
  }

  /**
   * Try to send each queued expense via ``sendOne(expense)``. Items the
   * sender resolves are removed; items that throw stay queued so the
   * next flush retries them. ``lastFlushError`` records the first
   * error of the sweep so the UI can surface it.
   */
  async function flush(sendOne) {
    lastFlushError.value = null;
    await refresh();
    for (const item of items.value) {
      try {
        await sendOne(item);
        await remove(item.id);
      } catch (err) {
        if (!lastFlushError.value) lastFlushError.value = err;
      }
    }
    return items.value;
  }

  return {
    items,
    lastFlushError,
    refresh,
    enqueue,
    remove,
    update,
    count,
    flush,
  };
});
