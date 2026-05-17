// Pinia store for the PWA currency picker.
//
// Owns:
//   * Saved-currency list (from /api/currencies).
//   * Last-used currency persisted in ``localStorage`` so the picker
//     starts on the operator's most-recent choice across reloads.
//
// The PWA does NOT keep exchange rates: conversion to the accounting
// currency is the server's responsibility and happens at write time
// inside ``POST /api/expenses``. Showing rates next to the picker
// chips would only be informational, was a constant background fetch
// on a battery-powered device, and would tempt the UI into
// double-converting amounts the server already converted. So the
// store is intentionally rate-free.

import { defineStore } from "pinia";
import { ref, computed } from "vue";

import * as currenciesApi from "../api/currencies.js";
import { useStaleCache } from "../composables/useStaleCache.js";

const LAST_USED_LS_KEY = "dinary.currency.lastUsed";
const DIRTY_KEY = "dinary:currency:dirty";
const FETCHED_KEY = "dinary:currency:fetchedAt";

function readLastUsed() {
  try {
    return localStorage.getItem(LAST_USED_LS_KEY) || null;
  } catch {
    return null;
  }
}

function writeLastUsed(code) {
  try {
    if (!code) {
      localStorage.removeItem(LAST_USED_LS_KEY);
    } else {
      localStorage.setItem(LAST_USED_LS_KEY, code);
    }
  } catch {
    // Quota / private mode: ignore — last-used falls back to default.
  }
}

export const useCurrencyStore = defineStore("currency", () => {
  const codes = ref([]);
  const defaultCode = ref("RSD");
  const lastUsed = ref(readLastUsed());
  const lastListError = ref(null);
  const { dirtyFlag, lastFetchedAt, markDirty, stampFresh: _stampFresh, isStale } = useStaleCache({
    dirtyKey: DIRTY_KEY,
    fetchedKey: FETCHED_KEY,
  });

  /**
   * Currency code the picker should default to. Order:
   *   1) Operator's last selection (persisted to localStorage).
   *   2) Server-reported ``default_code`` (= app_currency env var).
   *   3) Hard-coded ``"RSD"`` as a final fallback.
   */
  const preferredCode = computed(
    () =>
      (lastUsed.value && codes.value.includes(lastUsed.value) && lastUsed.value) ||
      defaultCode.value ||
      "RSD",
  );

  async function loadIfNeeded() {
    if (codes.value.length > 0 && !isStale()) return;
    try {
      const snap = await currenciesApi.fetchCurrencies();
      codes.value = Array.isArray(snap?.codes) ? snap.codes.slice() : [];
      defaultCode.value = snap?.default_code || "RSD";
      lastListError.value = null;
      _stampFresh();
    } catch (err) {
      lastListError.value = err;
      throw err;
    }
  }

  async function addCurrency(code) {
    const snap = await currenciesApi.addCurrency(code);
    codes.value = Array.isArray(snap?.codes) ? snap.codes.slice() : [];
    defaultCode.value = snap?.default_code || defaultCode.value;
    _stampFresh();
    return snap;
  }

  async function removeCurrency(code) {
    const snap = await currenciesApi.deleteCurrency(code);
    codes.value = Array.isArray(snap?.codes) ? snap.codes.slice() : [];
    defaultCode.value = snap?.default_code || defaultCode.value;
    _stampFresh();
    if (lastUsed.value && !codes.value.includes(lastUsed.value)) {
      setLastUsed(defaultCode.value);
    }
    return snap;
  }

  function setLastUsed(code) {
    const upper = typeof code === "string" ? code.toUpperCase() : null;
    lastUsed.value = upper;
    writeLastUsed(upper);
  }

  return {
    codes,
    defaultCode,
    lastUsed,
    lastListError,
    dirtyFlag,
    lastFetchedAt,
    preferredCode,
    markDirty,
    loadIfNeeded,
    addCurrency,
    removeCurrency,
    setLastUsed,
  };
});
