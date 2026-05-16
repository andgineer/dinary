// Offline queue for receipt URLs awaiting server-side classification.
// Uses a separate IndexedDB ("dinary-receipts") so this store is entirely
// independent of the expense queue — receipts carry no catalog state and
// need no catalog_version bump on flush.
import { defineStore } from "pinia";
import { ref } from "vue";

const DB_NAME = "dinary-receipts";
const DB_VERSION = 1;
const STORE_NAME = "pending_receipts";

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

function isDisconnectError(err) {
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
      const captured = _dbPromise;
      db.onclose = () => {
        if (_dbPromise === captured) _dbPromise = null;
      };
      db.onversionchange = () => {
        db.close();
        if (_dbPromise === captured) _dbPromise = null;
      };
      resolve(db);
    };
    req.onerror = () => {
      _dbPromise = null;
      reject(req.error);
    };
    req.onblocked = () => {
      _dbPromise = null;
      reject(new Error("indexedDB open blocked"));
    };
  });
  return _dbPromise;
}

function resetDb() {
  _dbPromise = null;
}

async function withDb(fn) {
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

async function urlToReceiptId(url) {
  const data = new TextEncoder().encode(url);
  const buf = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(buf))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

export const useReceiptQueueStore = defineStore("receiptQueue", () => {
  const items = ref([]);
  const lastFlushError = ref(null);

  async function refresh() {
    const all = await withDb(async (db) =>
      runTx(db, "readonly", (store) => reqToPromise(store.getAll())),
    );
    items.value = all ?? [];
    return items.value;
  }

  async function enqueue(url) {
    const existing = await withDb(async (db) =>
      runTx(db, "readonly", (store) => reqToPromise(store.getAll())),
    );
    if (existing.some((item) => item.url === url)) return;
    const clientReceiptId = await urlToReceiptId(url);
    await withDb(async (db) =>
      runTx(db, "readwrite", (store) =>
        store.add({ client_receipt_id: clientReceiptId, url, queued_at: Date.now() }),
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

  async function count() {
    const all = await withDb(async (db) =>
      runTx(db, "readonly", (store) => reqToPromise(store.getAll())),
    );
    return all.length;
  }

  return { items, lastFlushError, refresh, enqueue, remove, count };
});
