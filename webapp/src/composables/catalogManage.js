import { ref } from "vue";
import { useCatalogStore } from "../stores/catalog.js";
import { useToastStore } from "../stores/toast.js";

const ACTION_VERBS = {
  reactivate: "restore",
  deactivate: "hide",
  remove: "delete",
};

// Cross-cutting "manage mode + edit modal" concern shared by every catalog
// entity row in the form (group / category / event / tag). Owns three
// pieces of state and four actions:
//
//   * manageMode[kind]      — is the inline manage panel expanded?
//   * pendingManageId[kind] — id of the row currently being mutated, so
//                             ManageList can disable all sibling buttons
//                             in lockstep (matches the legacy
//                             "for (const s of siblings) s.disabled =
//                             true" behaviour).
//   * editModal             — { open, kind, item } payload for EditModal.
//
// runCatalogAction wraps catalog.deactivate / reactivate / remove,
// surfacing soft-delete vs hard-delete vs failure as toasts.
export function useCatalogManage() {
  const catalog = useCatalogStore();
  const toast = useToastStore();

  const manageMode = ref({ group: false, category: false, event: false, tag: false });
  const pendingManageId = ref({ group: null, category: null, event: null, tag: null });
  const editModal = ref({ open: false, kind: null, item: null });

  function toggleManage(kind) {
    manageMode.value[kind] = !manageMode.value[kind];
  }

  async function runCatalogAction(kind, item, action) {
    pendingManageId.value[kind] = item.id;
    try {
      const snap = await catalog[action](kind, item.id);
      if (action === "remove" && snap?.delete_status === "soft") {
        toast.show(
          `Not deleted: still used in ${snap.usage_count ?? 0} expenses. Kept hidden.`,
          "info",
        );
      } else if (action === "remove" && snap?.delete_status === "hard") {
        toast.show("Deleted permanently", "success");
      }
    } catch (err) {
      toast.show(`Failed to ${ACTION_VERBS[action]}: ${err?.message || err}`, "error");
    } finally {
      pendingManageId.value[kind] = null;
    }
  }

  function onEdit(kind, item) {
    editModal.value = { open: true, kind, item };
  }

  function closeEdit() {
    editModal.value = { open: false, kind: null, item: null };
  }

  return {
    manageMode,
    pendingManageId,
    editModal,
    toggleManage,
    runCatalogAction,
    onEdit,
    closeEdit,
  };
}
