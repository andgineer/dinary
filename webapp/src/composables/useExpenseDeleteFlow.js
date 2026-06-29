import { ref } from "vue";
import { useReviewStore } from "../stores/review.js";
import { useToastStore } from "../stores/toast.js";
import { getReceipt } from "../api/receipts.js";

export function useExpenseDeleteFlow({ getExpense, isManual, isReceiptBacked, onClose }) {
  const reviewStore = useReviewStore();
  const toast = useToastStore();

  const confirmingDelete = ref(false);
  const deleting = ref(false);
  const cascade = ref(null);
  const cascadeLoading = ref(false);

  function resetDeleteState() {
    confirmingDelete.value = false;
    cascade.value = null;
    cascadeLoading.value = false;
  }

  async function _fetchCascade() {
    cascadeLoading.value = true;
    try {
      cascade.value = await getReceipt(getExpense().receipt_id, { include: "expenses" });
    } catch {
      // cascade card will show loading until retry
    } finally {
      cascadeLoading.value = false;
    }
  }

  function openDeleteConfirm() {
    if (isReceiptBacked.value && !cascade.value) {
      _fetchCascade();
    }
    confirmingDelete.value = true;
  }

  async function confirmDelete() {
    if (!navigator.onLine) {
      toast.show("Not available offline", "error");
      return;
    }
    if (deleting.value) return;
    deleting.value = true;
    try {
      if (isManual.value) {
        await reviewStore.deleteExpense(getExpense().id);
        toast.show("Expense deleted", "info");
      } else {
        const receiptId = getExpense().receipt_id;
        const count = cascade.value?.expenses?.length ?? 0;
        await reviewStore.deleteReceipt(receiptId);
        toast.show(`Receipt deleted (${count} expense${count !== 1 ? "s" : ""} removed)`, "info");
      }
      window.dispatchEvent(new Event("online"));
      confirmingDelete.value = false;
      onClose();
    } catch (err) {
      toast.show(err?.message || "Delete failed", "error");
    } finally {
      deleting.value = false;
    }
  }

  function cancelDelete() {
    confirmingDelete.value = false;
  }

  return {
    confirmingDelete,
    deleting,
    cascade,
    cascadeLoading,
    resetDeleteState,
    openDeleteConfirm,
    confirmDelete,
    cancelDelete,
  };
}
