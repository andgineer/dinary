// Queue-flush helper. Lives outside the queue store so the store stays
// pure (no API imports). The form/app calls flushQueue() on save and
// on online events; the helper drains the queue store one item at a
// time via the expenses API and triggers a catalog refetch when the
// server hands back a newer catalog_version.

import { postExpense } from "../api/expenses.js";
import { useCatalogStore } from "../stores/catalog.js";
import { useQueueStore } from "../stores/queue.js";
import { useReviewStore } from "../stores/review.js";
import { useToastStore } from "../stores/toast.js";
let _inFlight = false;

export async function flushQueue() {
  if (_inFlight) return;
  _inFlight = true;
  const queue = useQueueStore();
  const catalog = useCatalogStore();
  const review = useReviewStore();
  const toast = useToastStore();
  await queue.refresh();
  let anyFlushed = false;
  let latestCatalogVersion = -1;
  try {
    for (const item of [...queue.items]) {
      if (typeof item.category_id !== "number") {
        // Pre-Phase-2 queue item without a category_id; the v2 server
        // would 422. Drop it with a one-line operator note.
        await queue.remove(item.id);
        toast.show("Dropped legacy queued entry (please re-enter)", "info");
        continue;
      }
      try {
        const resp = await postExpense({
          client_expense_id: item.client_expense_id,
          amount: item.amount,
          currency: item.currency || "RSD",
          category_id: item.category_id,
          event_id: item.event_id ?? null,
          tag_ids: item.tag_ids ?? [],
          comment: item.comment || "",
          expense_datetime: item.expense_datetime || `${item.date}T12:00:00+01:00`,
        });
        if (typeof resp?.catalog_version === "number") {
          latestCatalogVersion = Math.max(latestCatalogVersion, resp.catalog_version);
        }
        if (resp?.default_group_id != null || resp?.default_category_ids) {
          catalog.applyExpenseDefaults(resp);
        }
        if (Array.isArray(resp?.frequent_categories)) {
          catalog.applyFrequentCategories(resp.frequent_categories);
        }
        await queue.remove(item.id);
        anyFlushed = true;
      } catch (err) {
        if (err?.status === 409) {
          await queue.remove(item.id);
          toast.show("Expense already recorded with different data", "error");
          continue;
        }
        if (err?.status === 401 || err?.status === 302) {
          toast.show("Session expired — please re-open the app to log in", "error");
          break;
        }
        queue.lastFlushError = err;
        toast.show(err?.message || "Send failed", "error");
        break;
      }
    }
  } finally {
    _inFlight = false;
  }
  if (latestCatalogVersion > 0 && latestCatalogVersion !== catalog.catalogVersion) {
    await catalog.load();
  }
  if (anyFlushed) {
    review.resetExpenses();
  }
}

export function _resetForTest() {
  _inFlight = false;
}
