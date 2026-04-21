/**
 * Main app logic — wires together form, QR scanner, offline queue, and the 3D catalog.
 *
 * Phase 2 form layout:
 *   amount -> group -> category -> event -> tags (multi) -> comment -> date -> save
 *
 * "+ Новый" modals live in ``catalog-add.js`` and refresh the in-memory
 * snapshot on success.
 *
 * Defaults and auto-attach:
 *
 *   - On first paint we default to (group "еда", category "еда") when
 *     both rows are present and active; otherwise we leave the current
 *     browser default (first option of each dropdown).
 *   - Selecting a date where an ``auto_attach_enabled`` event is
 *     active auto-populates the Event dropdown with the shortest such
 *     event. Once the operator touches the Event dropdown manually we
 *     remember the override (``userEventOverride``) and stop auto-
 *     selecting for the rest of the form's lifetime.
 */
const APP_VERSION = "__VERSION__";

import {
  cachedCatalogVersion,
  postExpense,
} from "./api.js";
import {
  deactivateCategory,
  deactivateEvent,
  deactivateGroup,
  deactivateTag,
  deleteCategory,
  deleteEvent,
  deleteGroup,
  deleteTag,
  findCategoryById,
  findCategoryByName,
  findGroupByName,
  getActiveEvents,
  getAutoAttachEventsOn,
  getCategoriesByGroup,
  getGroups,
  getInactiveCategoriesByGroup,
  getInactiveEventsInWindow,
  getInactiveGroups,
  getInactiveTags,
  getLastError,
  getTags,
  loadCatalog,
  populateCategoryDropdown,
  populateEventDropdown,
  populateGroupDropdown,
  populateTagsList,
  reactivateCategory,
  reactivateEvent,
  reactivateGroup,
  reactivateTag,
  readSelectedTagIds,
} from "./catalog.js";
import {
  openAddCategory,
  openAddEvent,
  openAddGroup,
  openAddTag,
} from "./catalog-add.js";
import { enqueue, getAll, remove, count } from "./offline-queue.js";
import { startScanning, stop as stopScanner } from "./qr-scanner.js";

const $ = (sel) => document.querySelector(sel);

const DEFAULT_GROUP_NAME = "еда";
const DEFAULT_CATEGORY_NAME = "еда";

// Per-picker flags. We keep them at module scope because the pickers
// are re-populated from scratch on every catalog refresh and we want
// the operator's choice to survive those refreshes.
const _showInactive = {
  group: false,
  category: false,
  event: false,
  tag: false,
};

// Once the operator picks an event by hand we stop auto-selecting on
// subsequent date changes. Reset on successful save (new form).
let _userEventOverride = false;

function parseReceiptUrl(url) {
  const vl = new URL(url).searchParams.get("vl");
  if (!vl) throw new Error("No vl parameter");

  const bin = Uint8Array.from(atob(vl), (c) => c.charCodeAt(0));

  const view = new DataView(bin.buffer);
  const amountRaw = view.getBigUint64(25, true);
  const amount = Number(amountRaw) / 10000;

  const msHi = view.getUint32(33, false);
  const msLo = view.getUint32(37, false);
  const ms = msHi * 0x100000000 + msLo;
  const dt = new Date(ms);
  const date = dt.toISOString().slice(0, 10);

  return { amount, date };
}

let _flushing = false;
let _lastFlushError = null;

function today() {
  return new Date().toISOString().slice(0, 10);
}

function showToast(msg, type = "info") {
  const el = $("#toast");
  el.textContent = msg;
  el.className = `toast show ${type}`;
  const delay = msg.length > 60 ? 8000 : 3000;
  setTimeout(() => el.classList.remove("show"), delay);
}

async function updateQueueBadge() {
  const n = await count();
  const badge = $(".queue-badge");
  if (n > 0) {
    badge.textContent = `${n} queued`;
    badge.classList.add("visible");
  } else {
    badge.classList.remove("visible");
  }
}

async function maybeRefreshCatalog(latestVersion) {
  if (latestVersion > 0 && latestVersion !== cachedCatalogVersion()) {
    await loadCatalog();
    rerenderCatalogControls();
  }
}

async function flushQueue() {
  if (_flushing) return;
  _flushing = true;

  _lastFlushError = null;
  let sent = 0;
  let observedVersion = -1;
  const items = await getAll();
  for (const item of items) {
    if (typeof item.category_id !== "number") {
      console.warn("Dropping pre-v2 queue item (no category_id):", item);
      await remove(item.id);
      showToast("Dropped legacy queued entry (please re-enter)", "info");
      continue;
    }

    const clientExpenseId = item.client_expense_id;

    try {
      const resp = await postExpense({
        client_expense_id: clientExpenseId,
        amount: item.amount,
        currency: item.currency || "RSD",
        category_id: item.category_id,
        event_id: item.event_id ?? null,
        tag_ids: item.tag_ids ?? [],
        comment: item.comment || "",
        date: item.date,
      });
      observedVersion = resp.catalog_version ?? observedVersion;
      await remove(item.id);
      sent++;
    } catch (e) {
      if (e.status === 409) {
        console.error("Conflict for expense", clientExpenseId, e);
        await remove(item.id);
        showToast("Expense already recorded with different data", "error");
        continue;
      }
      if (e.status === 401 || e.status === 302) {
        showToast("Session expired — please re-open the app to log in", "error");
        break;
      }
      console.warn("Flush failed for item", item.id, e);
      _lastFlushError = e.message || "Send failed";
      showToast(_lastFlushError, "error");
      break;
    }
  }

  _flushing = false;
  await updateQueueBadge();
  if (observedVersion > 0) await maybeRefreshCatalog(observedVersion);
}

function getSelectedCategoryId() {
  const raw = $("#category").value;
  return raw ? Number(raw) : null;
}

function getSelectedGroupId() {
  const raw = $("#group").value;
  return raw ? Number(raw) : null;
}

function getSelectedEventId() {
  const raw = $("#event").value;
  return raw ? Number(raw) : null;
}

async function submitExpense() {
  const rawAmount = $("#amount").value.replace(",", ".").trim();
  const amount = parseFloat(rawAmount);
  const categoryId = getSelectedCategoryId();
  const eventId = getSelectedEventId();
  const tagIds = readSelectedTagIds($("#tags"));
  const comment = $("#comment").value.trim();
  const date = $("#date").value;

  if (!rawAmount || isNaN(amount) || amount <= 0) {
    showToast("Enter a valid amount", "error");
    return;
  }
  if (!categoryId) {
    showToast("Select a category", "error");
    return;
  }

  const entry = {
    amount,
    currency: "RSD",
    category_id: categoryId,
    event_id: eventId,
    tag_ids: tagIds,
    category_name: findCategoryById(categoryId)?.name || "",
    comment,
    date,
  };

  const btn = $("#save-btn");
  btn.disabled = true;

  try {
    await enqueue(entry);
    await updateQueueBadge();
  } catch (e) {
    showToast(`Save failed: ${e.message}`, "error");
    btn.disabled = false;
    return;
  }

  btn.textContent = "\u2713";
  btn.classList.add("btn-success");
  setTimeout(() => {
    btn.textContent = "Save";
    btn.classList.remove("btn-success");
    btn.disabled = false;
    resetForm();
    showToast(`${amount} RSD`, navigator.onLine ? "success" : "info");
    if (navigator.onLine) flushQueue();
  }, 800);
}

function resetForm() {
  $("#amount").value = "";
  $("#comment").value = "";
  $("#date").value = today();
  $("#event").value = "";
  for (const cb of $("#tags").querySelectorAll("input[type=checkbox]")) {
    cb.checked = false;
  }
  _userEventOverride = false;
  applyDefaultGroupAndCategory();
  rerenderEventDropdownForDate();
  $("#amount").focus();
}

async function handleQrScan() {
  const btn = $("#qr-btn");
  const video = $("#qr-video");

  if (video.style.display === "block") {
    stopScanner();
    video.style.display = "none";
    btn.textContent = "Scan QR";
    return;
  }

  video.style.display = "block";
  btn.textContent = "Stop";

  try {
    await startScanning(video, (text) => {
      video.style.display = "none";
      btn.textContent = "Scan QR";
      handleQrResult(text);
    });
  } catch (e) {
    showToast(e.message || "Camera failed", "error");
    video.style.display = "none";
    btn.textContent = "Scan QR";
  }
}

function handleQrResult(text) {
  if (!text.includes("suf.purs.gov.rs")) {
    const preview = text.length > 80 ? text.slice(0, 80) + "…" : text;
    showToast(`Not a fiscal QR: ${preview}`, "error");
    return;
  }
  try {
    const parsed = parseReceiptUrl(text);
    $("#amount").value = parsed.amount;
    $("#date").value = parsed.date;
    showToast(`Receipt: ${parsed.amount} RSD, ${parsed.date}`, "success");
    rerenderEventDropdownForDate();
    $("#group").focus();
  } catch {
    showToast("Could not read receipt", "error");
  }
}

function formatQueueItem(item) {
  const parts = [`${item.amount} RSD`, item.category_name || `cat#${item.category_id}`];
  if (item.comment) parts.push(item.comment);
  parts.push(item.date);
  return parts.join(" | ");
}

async function showQueueModal() {
  const items = await getAll();

  const list = $("#queue-list");
  if (items.length) {
    list.innerHTML = items
      .map(
        (it) =>
          `<div class="queue-item">
            <span class="qi-amount">${it.amount} RSD</span> — ${it.category_name || `cat#${it.category_id}`}
            ${it.comment ? `<br>${it.comment}` : ""}
            <div class="qi-meta">${it.date}</div>
          </div>`,
      )
      .join("");
    $("#queue-copy").style.display = "";
  } else {
    list.innerHTML = '<div style="color:#94a3b8;text-align:center">No queued expenses</div>';
    $("#queue-copy").style.display = "none";
  }

  const errEl = $("#queue-error");
  if (_lastFlushError && items.length) {
    errEl.textContent = _lastFlushError;
    errEl.style.display = "";
  } else {
    errEl.textContent = "";
    errEl.style.display = "none";
    _lastFlushError = null;
  }

  const vi = $("#version-info");
  vi.textContent = `v${APP_VERSION}`;
  if (navigator.onLine) {
    try {
      const resp = await fetch("/api/version");
      const { version: serverVer } = await resp.json();
      if (serverVer && serverVer !== APP_VERSION) {
        vi.innerHTML = `v${APP_VERSION} · <span style="color:#f59e0b">update available (${serverVer})</span>`;
      }
    } catch { /* ignore */ }
  }

  $("#queue-modal").style.display = "flex";

  $("#queue-copy").onclick = async () => {
    const text = items.map(formatQueueItem).join("\n");
    try {
      await navigator.clipboard.writeText(text);
      showToast("Copied to clipboard", "success");
    } catch {
      showToast("Copy failed", "error");
    }
  };
}

function closeQueueModal() {
  $("#queue-modal").style.display = "none";
}

function updateOnlineStatus() {
  const hint = $(".offline-hint");
  if (navigator.onLine) {
    hint.style.display = "none";
    flushQueue();
  } else {
    hint.style.display = "block";
  }
}

function startRetryTimer() {
  setInterval(async () => {
    if (navigator.onLine && (await count()) > 0) {
      flushQueue();
    }
  }, 30_000);
}

function refreshAddCategoryButton() {
  const btn = $("#add-category-btn");
  if (!btn) return;
  const hasGroup = Boolean($("#group").value);
  btn.disabled = !hasGroup;
  btn.title = hasGroup ? "Новая категория" : "Сначала выберите группу";
  const hint = $("#add-category-hint");
  if (hint) hint.hidden = hasGroup;
}

// ---------------------------------------------------------------------------
// Default (group, category) application
// ---------------------------------------------------------------------------

function applyDefaultGroupAndCategory() {
  const groupSelect = $("#group");
  const catSelect = $("#category");
  const group = findGroupByName(DEFAULT_GROUP_NAME);
  if (group && Array.from(groupSelect.options).some((o) => o.value === String(group.id))) {
    groupSelect.value = String(group.id);
    populateCategoryDropdown(catSelect, group.id);
    const cat = findCategoryByName(DEFAULT_CATEGORY_NAME, { groupId: group.id });
    if (cat && Array.from(catSelect.options).some((o) => o.value === String(cat.id))) {
      catSelect.value = String(cat.id);
    }
  }
  refreshAddCategoryButton();
}

// ---------------------------------------------------------------------------
// Event dropdown (date-anchored + auto-attach)
// ---------------------------------------------------------------------------

function rerenderEventDropdownForDate() {
  const dateStr = $("#date").value || undefined;
  const previous = $("#event").value;
  populateEventDropdown($("#event"), dateStr);
  if (previous) {
    const stillThere = Array.from($("#event").options).some(
      (o) => o.value === previous,
    );
    if (stillThere) {
      $("#event").value = previous;
    } else {
      showToast("Выбранное событие вне диапазона дат — сброшено", "info");
      _userEventOverride = false;
    }
  }
  if (!_userEventOverride) {
    applyAutoAttachEventForDate(dateStr);
  }
  renderInactiveList("event");
}

function applyAutoAttachEventForDate(dateStr) {
  // Pick the shortest auto-attach event active on ``dateStr``. If
  // nothing matches, clear the current selection (because a previous
  // auto-selection may have survived a date change into a no-trip
  // window). Never touch the dropdown when the user has explicitly
  // chosen an event.
  const selectEl = $("#event");
  const matches = getAutoAttachEventsOn(dateStr || today());
  if (matches.length === 0) {
    selectEl.value = "";
    return;
  }
  const pick = matches[0];
  if (Array.from(selectEl.options).some((o) => o.value === String(pick.id))) {
    selectEl.value = String(pick.id);
    return;
  }
  // The auto-attach resolver found an event whose date range covers
  // ``dateStr`` but the ±30d dropdown window in
  // ``populateEventDropdown`` is not wide enough to include it. This
  // is a configuration smell — a long-running event (e.g. a
  // year-long vacation) paired with an expense date far from today.
  // Surfacing it via console.warn keeps the auto-selection honest
  // instead of silently leaving the field blank.
  console.warn(
    "auto-attach event not in dropdown window",
    {
      date: dateStr,
      picked: { id: pick.id, name: pick.name, from: pick.dateFrom, to: pick.dateTo },
      dropdownSize: selectEl.options.length,
    },
  );
}

// ---------------------------------------------------------------------------
// Per-picker "Управлять" toggle — unified active + inactive management list.
//
// When the toggle is on we render two stacked lists under the picker:
//   • active rows with a "Скрыть" button (deactivate; stays in the DB,
//     disappears from normal dropdowns, can be re-surfaced via
//     "Активировать"); the "Удалить" button is shown *only* when the
//     server-precomputed ``removable`` flag says hard-delete would
//     actually succeed (no expenses or mapping references, for tags
//     additionally no event ``auto_tags`` membership). This makes the
//     most common scenario — removing something just created by
//     mistake — a single click, without forcing "скрыть -> удалить".
//   • inactive rows with an "Активировать" button; here too "Удалить"
//     is gated on ``removable``.
//
// The ``_showInactive`` flag name is legacy — we keep it because the
// toggle control still lives at ``#toggle-inactive-<kind>`` — but it
// now means "manage mode is on".
// ---------------------------------------------------------------------------

const MANAGE_CONFIG = {
  group: {
    containerId: "inactive-group-list",
    toggleId: "toggle-inactive-group",
    activeList: () => getGroups({ includeInactive: false }),
    inactiveList: () => getInactiveGroups(),
    label: (g) => g.name,
    reactivate: (id) => reactivateGroup(id),
    deactivate: (id) => deactivateGroup(id),
    remove: (id) => deleteGroup(id),
    kindNoun: "группа",
  },
  category: {
    containerId: "inactive-category-list",
    toggleId: "toggle-inactive-category",
    activeList: () => {
      const gid = getSelectedGroupId();
      return gid ? getCategoriesByGroup(gid, { includeInactive: false }) : [];
    },
    inactiveList: () => getInactiveCategoriesByGroup(getSelectedGroupId()),
    label: (c) => c.name,
    reactivate: (id) => reactivateCategory(id),
    deactivate: (id) => deactivateCategory(id),
    remove: (id) => deleteCategory(id),
    kindNoun: "категория",
  },
  event: {
    containerId: "inactive-event-list",
    toggleId: "toggle-inactive-event",
    activeList: () => getActiveEvents($("#date").value || today()),
    inactiveList: () => getInactiveEventsInWindow($("#date").value || today()),
    label: (e) => `${e.name} (${e.date_from}..${e.date_to})`,
    reactivate: (id) => reactivateEvent(id),
    deactivate: (id) => deactivateEvent(id),
    remove: (id) => deleteEvent(id),
    kindNoun: "событие",
  },
  tag: {
    containerId: "inactive-tag-list",
    toggleId: "toggle-inactive-tag",
    activeList: () => getTags({ includeInactive: false }),
    inactiveList: () => getInactiveTags(),
    label: (t) => t.name,
    reactivate: (id) => reactivateTag(id),
    deactivate: (id) => deactivateTag(id),
    remove: (id) => deleteTag(id),
    kindNoun: "тэг",
  },
};

function appendDeleteButton(row, cfg, it, siblings) {
  // ``removable`` is the server-computed "hard-delete would succeed"
  // flag (``build_catalog_snapshot``). If it's false — i.e. there
  // are expenses, mapping-table refs, or (for tags) events with the
  // tag in ``auto_tags`` — the button is not rendered so the
  // operator doesn't get a confusing "нельзя удалить, всё ещё
  // используется" dead-end. They can "Скрыть" instead.
  if (it.removable !== true) return;
  const delBtn = document.createElement("button");
  delBtn.type = "button";
  delBtn.className = "btn-inline inactive-delete";
  delBtn.textContent = "Удалить";
  delBtn.title = "Удалить окончательно (без ссылок на эту строку)";
  delBtn.onclick = async () => {
    if (!window.confirm(`Удалить «${cfg.label(it)}» окончательно?`)) return;
    delBtn.disabled = true;
    for (const s of siblings) s.disabled = true;
    try {
      const snap = await cfg.remove(it.id);
      if (snap?.delete_status === "soft") {
        // Race: something started referencing the row between the
        // snapshot that set ``removable=true`` and this DELETE
        // landing. We fall back to the legacy soft-delete toast.
        const n = snap.usage_count ?? 0;
        showToast(
          `Не удалено: ещё используется в ${n} расходах. Осталось скрытым.`,
          "info",
        );
      } else if (snap?.delete_status === "hard") {
        showToast("Удалено окончательно", "success");
      }
      rerenderCatalogControls();
    } catch (e) {
      showToast(`Не удалось удалить: ${e.message}`, "error");
      delBtn.disabled = false;
      for (const s of siblings) s.disabled = false;
    }
  };
  row.appendChild(delBtn);
}

function appendRow(container, cfg, it, primary) {
  const row = document.createElement("div");
  row.className =
    primary === "deactivate" ? "inactive-row active-row" : "inactive-row";
  const name = document.createElement("span");
  name.className = "inactive-name";
  name.textContent = cfg.label(it);
  row.appendChild(name);

  const primaryBtn = document.createElement("button");
  primaryBtn.type = "button";
  primaryBtn.className =
    primary === "deactivate" ? "btn-inline inactive-hide" : "btn-inline inactive-activate";
  primaryBtn.textContent = primary === "deactivate" ? "Скрыть" : "Активировать";
  primaryBtn.onclick = async () => {
    primaryBtn.disabled = true;
    try {
      if (primary === "deactivate") {
        await cfg.deactivate(it.id);
      } else {
        await cfg.reactivate(it.id);
      }
      rerenderCatalogControls();
    } catch (e) {
      const verb = primary === "deactivate" ? "скрыть" : "активировать";
      showToast(`Не удалось ${verb}: ${e.message}`, "error");
      primaryBtn.disabled = false;
    }
  };
  row.appendChild(primaryBtn);
  appendDeleteButton(row, cfg, it, [primaryBtn]);
  container.appendChild(row);
}

function appendSectionHeader(container, text) {
  const h = document.createElement("div");
  h.className = "inactive-section";
  h.textContent = text;
  container.appendChild(h);
}

function renderManageList(kind) {
  const cfg = MANAGE_CONFIG[kind];
  const container = document.getElementById(cfg.containerId);
  if (!container) return;
  const toggle = document.getElementById(cfg.toggleId);
  if (!_showInactive[kind]) {
    container.hidden = true;
    container.innerHTML = "";
    if (toggle) toggle.textContent = "Управлять";
    return;
  }
  if (toggle) toggle.textContent = "Закрыть";
  container.hidden = false;
  container.innerHTML = "";

  const active = cfg.activeList();
  appendSectionHeader(container, "Активные");
  if (active.length === 0) {
    const empty = document.createElement("div");
    empty.className = "inactive-empty";
    empty.textContent = "— нет активных —";
    container.appendChild(empty);
  } else {
    for (const it of active) appendRow(container, cfg, it, "deactivate");
  }

  const inactive = cfg.inactiveList();
  appendSectionHeader(container, "Неактивные");
  if (inactive.length === 0) {
    const empty = document.createElement("div");
    empty.className = "inactive-empty";
    empty.textContent = "— нет неактивных —";
    container.appendChild(empty);
  } else {
    for (const it of inactive) appendRow(container, cfg, it, "reactivate");
  }
}

// Legacy alias — external callers still invoke the old name.
function renderInactiveList(kind) {
  renderManageList(kind);
}

function wireInactiveToggles() {
  for (const kind of Object.keys(MANAGE_CONFIG)) {
    const toggle = document.getElementById(MANAGE_CONFIG[kind].toggleId);
    if (!toggle) continue;
    toggle.addEventListener("click", () => {
      _showInactive[kind] = !_showInactive[kind];
      renderManageList(kind);
    });
  }
}

function rerenderInactiveLists() {
  for (const kind of Object.keys(MANAGE_CONFIG)) {
    renderManageList(kind);
  }
}

function rerenderCatalogControls() {
  const currentGroup = $("#group").value;
  populateGroupDropdown($("#group"));
  if (currentGroup) {
    if (Array.from($("#group").options).some((o) => o.value === currentGroup)) {
      $("#group").value = currentGroup;
    }
  } else {
    applyDefaultGroupAndCategory();
  }
  populateCategoryDropdown($("#category"), $("#group").value);
  rerenderEventDropdownForDate();
  populateTagsList($("#tags"));
  refreshAddCategoryButton();
  rerenderInactiveLists();
}

async function init() {
  $("#date").value = today();

  await loadCatalog();
  const catErr = getLastError();
  if (catErr) {
    showToast(`Catalog: ${catErr.message}`, "error");
  }
  populateGroupDropdown($("#group"));
  applyDefaultGroupAndCategory();
  rerenderEventDropdownForDate();
  populateTagsList($("#tags"));
  refreshAddCategoryButton();
  rerenderInactiveLists();

  $("#group").addEventListener("change", (e) => {
    populateCategoryDropdown($("#category"), e.target.value);
    refreshAddCategoryButton();
    renderInactiveList("category");
  });

  $("#date").addEventListener("change", rerenderEventDropdownForDate);
  $("#event").addEventListener("change", () => {
    _userEventOverride = true;
  });

  $("#save-btn").addEventListener("click", submitExpense);
  $("#qr-btn").addEventListener("click", handleQrScan);
  $(".queue-badge").addEventListener("click", showQueueModal);
  $("#queue-modal-close").addEventListener("click", closeQueueModal);
  $("#queue-modal").addEventListener("click", (e) => {
    if (e.target === e.currentTarget) closeQueueModal();
  });

  $("#add-group-btn").addEventListener("click", () =>
    openAddGroup((newId) => {
      rerenderCatalogControls();
      if (newId) $("#group").value = String(newId);
      populateCategoryDropdown($("#category"), $("#group").value);
    }),
  );
  $("#add-category-btn").addEventListener("click", () =>
    openAddCategory(getSelectedGroupId(), (newId) => {
      rerenderCatalogControls();
      if (getSelectedGroupId()) $("#group").value = String(getSelectedGroupId());
      populateCategoryDropdown($("#category"), $("#group").value);
      if (newId) $("#category").value = String(newId);
    }),
  );
  $("#add-event-btn").addEventListener("click", () =>
    openAddEvent((newId) => {
      rerenderEventDropdownForDate();
      if (newId) {
        $("#event").value = String(newId);
        _userEventOverride = true;
      }
    }),
  );
  $("#add-tag-btn").addEventListener("click", () =>
    openAddTag(() => populateTagsList($("#tags"))),
  );

  wireInactiveToggles();

  document.addEventListener("dinary:catalog-add-result", (e) => {
    const { message } = e.detail || {};
    if (message) showToast(message, "info");
  });

  window.addEventListener("online", updateOnlineStatus);
  window.addEventListener("offline", updateOnlineStatus);
  updateOnlineStatus();

  await updateQueueBadge();
  startRetryTimer();

  $("#header-version").textContent = `v${APP_VERSION}`;

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }
}

init();
