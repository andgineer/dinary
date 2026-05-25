import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useCatalogManage } from "../src/composables/catalogManage.js";
import { useCatalogStore } from "../src/stores/catalog.js";
import { useToastStore } from "../src/stores/toast.js";

beforeEach(async () => {
  await allure.epic("Composables");
  await allure.feature("useCatalogManage");
});

beforeEach(() => {
  setActivePinia(createPinia());
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useCatalogManage — manage mode", () => {
  it("starts with every kind closed", () => {
    const m = useCatalogManage();
    expect(m.manageMode.value).toEqual({
      group: false,
      category: false,
      event: false,
      tag: false,
    });
  });

  it("toggleManage flips one kind without touching the others", () => {
    const m = useCatalogManage();
    m.toggleManage("group");
    expect(m.manageMode.value.group).toBe(true);
    expect(m.manageMode.value.category).toBe(false);
    m.toggleManage("group");
    expect(m.manageMode.value.group).toBe(false);
  });
});

describe("useCatalogManage — edit modal", () => {
  it("starts closed with empty payload", () => {
    const m = useCatalogManage();
    expect(m.editModal.value).toEqual({ open: false, kind: null, item: null });
  });

  it("onEdit + closeEdit flip the modal payload", () => {
    const m = useCatalogManage();
    m.onEdit("event", { id: 7, name: "trip" });
    expect(m.editModal.value).toEqual({
      open: true,
      kind: "event",
      item: { id: 7, name: "trip" },
    });
    m.closeEdit();
    expect(m.editModal.value).toEqual({ open: false, kind: null, item: null });
  });
});

describe("useCatalogManage — runCatalogAction", () => {
  it("sets pendingManageId during the call and clears it on success", async () => {
    const catalog = useCatalogStore();
    let resolveDeactivate;
    catalog.deactivate = vi.fn(
      () => new Promise((res) => { resolveDeactivate = res; }),
    );
    const m = useCatalogManage();
    const promise = m.runCatalogAction("group", { id: 5 }, "deactivate");
    expect(m.pendingManageId.value.group).toBe(5);
    resolveDeactivate({ delete_status: null });
    await promise;
    expect(m.pendingManageId.value.group).toBe(null);
  });

  it("emits the soft-delete toast when remove leaves the row hidden", async () => {
    const catalog = useCatalogStore();
    catalog.remove = vi.fn(async () => ({
      delete_status: "soft",
      usage_count: 3,
    }));
    const toast = useToastStore();
    const showSpy = vi.spyOn(toast, "show");
    const m = useCatalogManage();
    await m.runCatalogAction("event", { id: 1 }, "remove");
    expect(showSpy).toHaveBeenCalledWith(
      "Not deleted: still used in 3 expenses. Kept hidden.",
      "info",
    );
  });

  it("emits the hard-delete toast when remove fully erases the row", async () => {
    const catalog = useCatalogStore();
    catalog.remove = vi.fn(async () => ({ delete_status: "hard" }));
    const toast = useToastStore();
    const showSpy = vi.spyOn(toast, "show");
    const m = useCatalogManage();
    await m.runCatalogAction("tag", { id: 9 }, "remove");
    expect(showSpy).toHaveBeenCalledWith("Deleted permanently", "success");
  });

  it("translates the action verb when the catalog call rejects", async () => {
    const catalog = useCatalogStore();
    catalog.deactivate = vi.fn(async () => {
      throw new Error("nope");
    });
    const toast = useToastStore();
    const showSpy = vi.spyOn(toast, "show");
    const m = useCatalogManage();
    await m.runCatalogAction("group", { id: 1 }, "deactivate");
    expect(showSpy).toHaveBeenCalledWith("Failed to hide: nope", "error");
    expect(m.pendingManageId.value.group).toBe(null);
  });

  it("uses 'restore' / 'delete' verbs for reactivate / remove failures", async () => {
    const catalog = useCatalogStore();
    catalog.reactivate = vi.fn(async () => { throw new Error("x"); });
    catalog.remove = vi.fn(async () => { throw new Error("y"); });
    const toast = useToastStore();
    const showSpy = vi.spyOn(toast, "show");
    const m = useCatalogManage();
    await m.runCatalogAction("group", { id: 1 }, "reactivate");
    await m.runCatalogAction("group", { id: 2 }, "remove");
    expect(showSpy).toHaveBeenCalledWith("Failed to restore: x", "error");
    expect(showSpy).toHaveBeenCalledWith("Failed to delete: y", "error");
  });
});
