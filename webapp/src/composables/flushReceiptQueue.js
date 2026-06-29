// Flush the offline receipt URL queue to the server.
// Silently retains items on network failure so they are retried on the
// next online event or retry timer tick.  Duplicate responses (200/409)
// are treated as success and removed from the local queue.

import { postReceipt } from "../api/receipts.js";
import { useReceiptQueueStore } from "../stores/receiptQueue.js";
import { useToastStore } from "../stores/toast.js";
import { useLlmStore } from "../stores/llm.js";
import { useReviewStore } from "../stores/review.js";
import { parseReceiptUrl } from "./receipt.js";
import { reportNetworkFailure, reportNetworkSuccess } from "./swHealth.js";

let _inFlight = false;

export async function flushReceiptQueue() {
  if (_inFlight) return;
  _inFlight = true;
  const queue = useReceiptQueueStore();
  const toast = useToastStore();
  queue.lastFlushError = null;
  await queue.refresh();
  try {
    for (const item of [...queue.items]) {
      try {
        const body = await postReceipt({ client_receipt_id: item.client_receipt_id, url: item.url });
        await queue.remove(item.id);
        reportNetworkSuccess();
        let amountLabel = "";
        try {
          const { amount } = parseReceiptUrl(item.url);
          amountLabel = ` · ${amount.toLocaleString()} RSD`;
        } catch { /* URL may not be decodable */ }
        if (body?.status === "duplicate") {
          toast.show(`Receipt already recorded${amountLabel}`, "info");
        } else {
          useLlmStore().markDirty();
          useReviewStore().markDirty();
          toast.show(`Receipt saved${amountLabel}`, "success");
        }
      } catch (err) {
        // 409 conflict — receipt already registered with a different URL; discard locally.
        if (err?.status === 409) {
          await queue.remove(item.id);
          continue;
        }
        // Transient error — keep item, stop this sweep.
        if (err instanceof TypeError) reportNetworkFailure();
        queue.lastFlushError = err;
        toast.show(err?.message || "Receipt send failed", "error");
        break;
      }
    }
  } finally {
    _inFlight = false;
  }
}

export function _resetForTest() {
  _inFlight = false;
}
