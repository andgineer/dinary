/**
 * Main app logic — wires together form, QR scanner, offline queue, and categories.
 */

import { postExpense, parseQr } from "./api.js";
import {
  loadCategories,
  getLastError,
  populateGroupDropdown,
  populateCategoryDropdown,
  selectDefaults,
} from "./categories.js";
import { enqueue, getAll, remove, count } from "./offline-queue.js";
import { startScanning, stop as stopScanner } from "./qr-scanner.js";

const $ = (sel) => document.querySelector(sel);

let _flushing = false;

function today() {
  return new Date().toISOString().slice(0, 10);
}

function showToast(msg, type = "info") {
  const el = $("#toast");
  el.textContent = msg;
  el.className = `toast show ${type}`;
  setTimeout(() => el.classList.remove("show"), 3000);
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

  const items = await getAll();
  for (const item of items) {
    try {
      await postExpense({
        amount: item.amount,
        currency: item.currency || "RSD",
        category: item.category,
        group: item.group || "",
        comment: item.comment || "",
        date: item.date,
      });
      await remove(item.id);
    } catch (e) {
      if (e.message && (e.message.includes("401") || e.message.includes("302"))) {
        showToast("Session expired — please re-open the app to log in", "error");
        break;
      }
      console.warn("Flush failed for item", item.id, e);
      break;
    }
  }

  _flushing = false;
  await updateQueueBadge();
}

async function submitExpense() {
  const amount = parseFloat($("#amount").value);
  const group = $("#group").value;
  const category = $("#category").value;
  const comment = $("#comment").value.trim();
  const date = $("#date").value;

  if (!amount || amount <= 0) {
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

  if (!navigator.onLine) {
    await enqueue(entry);
    showToast("Saved offline — will sync when connected", "info");
    resetForm();
    btn.disabled = false;
    await updateQueueBadge();
    return;
  }

  try {
    const result = await postExpense(entry);
    showToast(
      `${result.amount_rsd} RSD → ${result.category} (total: ${result.new_total_rsd})`,
      "success",
    );
    resetForm();
  } catch {
    await enqueue(entry);
    showToast("Server error — entry queued for retry", "error");
    await updateQueueBadge();
  }

  btn.disabled = false;
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
  const reader = $("#qr-reader");

  if (reader.style.display === "block") {
    stopScanner();
    reader.style.display = "none";
    btn.textContent = "Scan QR";
    return;
  }

  if (!navigator.onLine) {
    showToast("QR scanning requires internet connection", "error");
    return;
  }

  reader.style.display = "block";
  btn.textContent = "Stop Scanner";

  try {
    await startScanning("qr-reader", async (text) => {
      reader.style.display = "none";
      btn.textContent = "Scan QR";

      if (!text.includes("suf.purs.gov.rs")) {
        showToast("Not a Serbian fiscal receipt QR code", "error");
        return;
      }

      btn.disabled = true;
      try {
        const result = await parseQr(text);
        $("#amount").value = result.amount;
        $("#date").value = result.date;
        showToast(`Receipt: ${result.amount} RSD, ${result.date}`, "success");
        $("#group").focus();
      } catch {
        showToast("Could not read receipt — try manual entry", "error");
      }
      btn.disabled = false;
    });
  } catch (e) {
    showToast(e.message || "Camera access failed", "error");
    reader.style.display = "none";
    btn.textContent = "Scan QR";
  }
}

function updateOnlineStatus() {
  const qrBtn = $("#qr-btn");
  const hint = $(".offline-hint");
  if (navigator.onLine) {
    qrBtn.disabled = false;
    hint.style.display = "none";
    flushQueue();
  } else {
    qrBtn.disabled = true;
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

  window.addEventListener("online", updateOnlineStatus);
  window.addEventListener("offline", updateOnlineStatus);
  updateOnlineStatus();

  await updateQueueBadge();
  startRetryTimer();

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }
}

init();
