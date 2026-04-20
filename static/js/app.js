/**
 * Main app logic — wires together form, QR scanner, offline queue, and the 3D catalog.
 *
 * Phase 2 form layout:
 *   amount -> group -> category -> event -> tags (multi) -> comment -> date -> save
 *
 * + Новый modals live in ``catalog-add.js`` and refresh the in-memory
 * snapshot on success.
 */
const APP_VERSION = "__VERSION__";

import {
  cachedCatalogVersion,
  postExpense,
} from "./api.js";
import {
  findCategoryById,
  getLastError,
  loadCatalog,
  populateCategoryDropdown,
  populateEventDropdown,
  populateGroupDropdown,
  populateTagsList,
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
    // Defensive migration: Phase 2 items carry ``category_id`` (a number).
    // A pre-v2 entry that somehow survived the IndexedDB upgrade only
    // has ``category`` / ``group`` *names* and would 422 on every
    // flush cycle, wedging the queue. Drop it and surface once.
    if (typeof item.category_id !== "number") {
      console.warn("Dropping pre-v2 queue item (no category_id):", item);
      await remove(item.id);
      showToast("Dropped legacy queued entry (please re-enter)", "info");
      continue;
    }

    // ``enqueue`` stamps ``client_expense_id`` at write time (v2+ schema)
    // and the ``upgrade`` callback drops any v1 leftovers, so a v2 item
    // in the queue always carries its own idempotency key. No runtime
    // fallback is needed here.
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
    // Denormalised labels so the queue modal can render without touching
    // the catalog snapshot.
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
    // Enqueue failed before the expense was durable — must not show
    // the success animation, must not reset the form, must not
    // schedule a flush. Re-enable the Save button so the user can
    // retry and return early.
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
  // The "+ Новая категория" modal requires a selected group because
  // ``catalog_writer.add_category`` needs a group_id. Rather than
  // opening the modal and erroring after the user types a name,
  // disable the trigger until a group is chosen.
  const btn = $("#add-category-btn");
  if (!btn) return;
  const hasGroup = Boolean($("#group").value);
  btn.disabled = !hasGroup;
  btn.title = hasGroup ? "Новая категория" : "Сначала выберите группу";
  // Also toggle the always-visible hint below the category dropdown
  // so mobile users (who have no hover-tooltip affordance) actually
  // see *why* the trigger is disabled.
  const hint = $("#add-category-hint");
  if (hint) hint.hidden = hasGroup;
}

function rerenderEventDropdownForDate() {
  // Feed the current value of the date picker into the catalog's
  // ±30-day event filter so back- or forward-dating an expense surfaces
  // the events active around *that* date rather than around the
  // day the page happened to load.
  const dateStr = $("#date").value || undefined;
  const previous = $("#event").value;
  populateEventDropdown($("#event"), dateStr);
  if (previous) {
    // Preserve the operator's selection if the event is still in
    // range after the date change; otherwise fall through to "none"
    // *and* tell the operator — a silent drop lets them submit with
    // "нет события" attached when they thought a specific event was
    // selected.
    const stillThere = Array.from($("#event").options).some(
      (o) => o.value === previous,
    );
    if (stillThere) {
      $("#event").value = previous;
    } else {
      showToast("Выбранное событие вне диапазона дат — сброшено", "info");
    }
  }
}

function rerenderCatalogControls() {
  const currentGroup = $("#group").value;
  populateGroupDropdown($("#group"));
  if (currentGroup) $("#group").value = currentGroup;
  populateCategoryDropdown($("#category"), $("#group").value);
  rerenderEventDropdownForDate();
  populateTagsList($("#tags"));
  refreshAddCategoryButton();
}

async function init() {
  $("#date").value = today();

  await loadCatalog();
  const catErr = getLastError();
  if (catErr) {
    showToast(`Catalog: ${catErr.message}`, "error");
  }
  rerenderCatalogControls();

  $("#group").addEventListener("change", (e) => {
    populateCategoryDropdown($("#category"), e.target.value);
    refreshAddCategoryButton();
  });

  $("#date").addEventListener("change", rerenderEventDropdownForDate);

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
      if (newId) $("#event").value = String(newId);
    }),
  );
  $("#add-tag-btn").addEventListener("click", () =>
    openAddTag(() => populateTagsList($("#tags"))),
  );

  // Surface ``AddResult.status`` from the catalog-add flow. "created"
  // is unremarkable (no toast); "reactivated" and "noop" tell the
  // operator that typing the same name didn't insert a brand-new row,
  // so they don't later wonder why the dropdown still has only one
  // entry (noop) or why a retired item came back with stale dates
  // (reactivated — they'd need to PATCH it via a future edit UI).
  document.addEventListener("dinary:catalog-add-result", (e) => {
    // ``message`` is pre-rendered by ``catalog-add.js`` so the Russian
    // grammatical agreement matches the entity kind. Templates are
    // only provided for ``reactivated`` / ``noop``; ``created`` is
    // deliberately silent (it's the unsurprising happy path).
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
