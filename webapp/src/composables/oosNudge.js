import { useToastStore } from "../stores/toast.js";

const STORAGE_KEY = "dinary:catalog:oosActivations";
const WINDOW_MS = 30 * 24 * 60 * 60 * 1000;
const NUDGE_THRESHOLD = 3;
const NUDGE_MESSAGE =
  "You've added several categories outside your set — open the category " +
  "picker's Manage → Switch category set to see other sets.";

function readActivations() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function writeActivations(timestamps) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(timestamps));
  } catch {
    // Quota / private mode: harmless, the nudge is best-effort.
  }
}

// Tracks out-of-set activations from CategorySheet's "Not in your set"
// search section. After 3 activations within 30 days, shows an info toast
// once and resets the counter so the next nudge requires 3 fresh activations.
export function recordOutOfSetActivation() {
  const now = Date.now();
  const cutoff = now - WINDOW_MS;
  const pruned = readActivations().filter((ts) => ts >= cutoff);
  pruned.push(now);

  if (pruned.length >= NUDGE_THRESHOLD) {
    useToastStore().show(NUDGE_MESSAGE, "info");
    writeActivations([]);
    return;
  }
  writeActivations(pruned);
}
