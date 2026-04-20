/**
 * Offline queue backed by IndexedDB.
 * Queues expense entries when offline or when the backend returns an error.
 * Flushes automatically when connectivity is restored.
 */

const DB_NAME = "dinary";
// v1 -> v2: Phase 2 3D catalog. Items stored by the v1 PWA carry
// ``category`` / ``group`` *names* and no ``category_id``; the v2
// server rejects those with 422. We drop the store's contents on
// upgrade rather than silently re-queue-then-fail every cycle. For a
// single-user deployment the worst case is a small number of
// offline-typed entries needing to be re-entered once.
const DB_VERSION = 2;
const STORE_NAME = "pending_expenses";

let _db = null;

function openDb() {
  if (_db) return Promise.resolve(_db);
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = (evt) => {
      const db = req.result;
      if (evt.oldVersion < 1) {
        db.createObjectStore(STORE_NAME, {
          keyPath: "id",
          autoIncrement: true,
        });
      }
      if (evt.oldVersion < 2 && db.objectStoreNames.contains(STORE_NAME)) {
        const tx = req.transaction;
        tx.objectStore(STORE_NAME).clear();
      }
    };
    req.onsuccess = () => {
      _db = req.result;
      resolve(_db);
    };
    req.onerror = () => reject(req.error);
  });
}

export async function enqueue(expense) {
  const db = await openDb();
  // The idempotency key is stamped at enqueue time, not at flush time,
  // so a single user action maps to exactly one ``client_expense_id``
  // even across flush retries and app restarts. Callers may pre-seed
  // the id (tests do this); otherwise we mint a fresh UUID here.
  const clientExpenseId = expense.client_expense_id || crypto.randomUUID();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readwrite");
    tx.objectStore(STORE_NAME).add({
      ...expense,
      client_expense_id: clientExpenseId,
      queued_at: Date.now(),
    });
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

export async function getAll() {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readonly");
    const req = tx.objectStore(STORE_NAME).getAll();
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

export async function remove(id) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readwrite");
    tx.objectStore(STORE_NAME).delete(id);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

export async function update(item) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readwrite");
    tx.objectStore(STORE_NAME).put(item);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

export async function count() {
  const items = await getAll();
  return items.length;
}
