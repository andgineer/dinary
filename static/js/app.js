/**
 * Main app logic — wires together form, QR scanner, offline queue, and categories.
 */
const APP_VERSION = "__VERSION__";

import { postExpense } from "./api.js";
import {
  loadCategories,
  getLastError,
  populateGroupDropdown,
  populateCategoryDropdown,
  selectDefaults,
} from "./categories.js";
import { enqueue, getAll, remove, update, count } from "./offline-queue.js";
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

async function flushQueue() {
  if (_flushing) return;
  _flushing = true;

  let sent = 0;
  const items = await getAll();
  for (const item of items) {
    const expenseId = item.expense_id || crypto.randomUUID();

    if (!item.expense_id) {
      await update({ ...item, expense_id: expenseId });
    }

    try {
      await postExpense({
        expense_id: expenseId,
        amount: item.amount,
        currency: item.currency || "RSD",
        category: item.category,
        group: item.group || "",
        comment: item.comment || "",
        date: item.date,
      });
      await remove(item.id);
      sent++;
    } catch (e) {
      if (e.message && e.message.includes("409")) {
        console.error("Conflict for expense", expenseId, e);
        await remove(item.id);
        showToast("Expense already recorded with different data", "error");
        continue;
      }
      if (e.message && (e.message.includes("401") || e.message.includes("302"))) {
        showToast("Session expired — please re-open the app to log in", "error");
        break;
      }
      console.warn("Flush failed for item", item.id, e);
      break;
    }
  }

  if (sent > 0) {
    const remaining = await count();
    if (remaining === 0) {
      showToast(`Sent ${sent} expense${sent > 1 ? "s" : ""}`, "success");
    } else {
      showToast(`Sent ${sent}, ${remaining} still queued`, "info");
    }
  }

  _flushing = false;
  await updateQueueBadge();
}

async function submitExpense() {
  const rawAmount = $("#amount").value.replace(",", ".").trim();
  const amount = parseFloat(rawAmount);
  const group = $("#group").value;
  const category = $("#category").value;
  const comment = $("#comment").value.trim();
  const date = $("#date").value;

  if (!rawAmount || isNaN(amount) || amount <= 0) {
    showToast("Enter a valid amount", "error");
    return;
  }
  if (!category) {
    showToast("Select a category", "error");
    return;
  }

  const entry = { amount, currency: "RSD", category, group, comment, date };

  const btn = $("#save-btn");
  btn.disabled = true;

  try {
    await enqueue(entry);
    await updateQueueBadge();
    resetForm();
  } catch (e) {
    showToast(`Save failed: ${e.message}`, "error");
    btn.disabled = false;
    return;
  }

  btn.disabled = false;

  if (!navigator.onLine) {
    showToast("Saved offline — will sync when connected", "info");
    return;
  }

  await flushQueue();
}

function resetForm() {
  $("#amount").value = "";
  $("#comment").value = "";
  $("#date").value = today();
  selectDefaults($("#group"), $("#category"));
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
  const parts = [`${item.amount} RSD`, item.category];
  if (item.group) parts.push(item.group);
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
            <span class="qi-amount">${it.amount} RSD</span> — ${it.category}${it.group ? ` / ${it.group}` : ""}
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

async function init() {
  $("#date").value = today();

  await loadCategories();
  const catErr = getLastError();
  if (catErr) {
    showToast(`Categories: ${catErr.message}`, "error");
  }
  populateGroupDropdown($("#group"));
  selectDefaults($("#group"), $("#category"));

  $("#group").addEventListener("change", (e) => {
    populateCategoryDropdown($("#category"), e.target.value);
  });

  $("#save-btn").addEventListener("click", submitExpense);
  $("#qr-btn").addEventListener("click", handleQrScan);
  $(".queue-badge").addEventListener("click", showQueueModal);
  $("#queue-modal-close").addEventListener("click", closeQueueModal);
  $("#queue-modal").addEventListener("click", (e) => {
    if (e.target === e.currentTarget) closeQueueModal();
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
