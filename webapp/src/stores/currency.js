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

import * as currenciesApi from "../api/currencies.js";

const LAST_USED_LS_KEY = "dinary.currency.lastUsed";

function readLastUsedFromStorage() {
  try {
    return localStorage.getItem(LAST_USED_LS_KEY) || null;
  } catch {
    return null;
  }
}

function writeLastUsedToStorage(code) {
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

export const useCurrencyStore = defineStore("currency", {
  state: () => ({
    codes: [],
    defaultCode: "RSD",
    lastUsed: readLastUsedFromStorage(),
    lastListError: null,
  }),

  getters: {
    /**
     * Currency code the picker should default to. Order:
     *   1) Operator's last selection (persisted to localStorage).
     *   2) Server-reported ``default_code`` (= app_currency env var).
     *   3) Hard-coded ``"RSD"`` as a final fallback.
     */
    preferredCode(state) {
      return (
        (state.lastUsed && state.codes.includes(state.lastUsed) && state.lastUsed) ||
        state.defaultCode ||
        "RSD"
      );
    },
  },

  actions: {
    async load() {
      try {
        const snap = await currenciesApi.fetchCurrencies();
        this.codes = Array.isArray(snap?.codes) ? snap.codes.slice() : [];
        this.defaultCode = snap?.default_code || "RSD";
        this.lastListError = null;
      } catch (err) {
        this.lastListError = err;
        throw err;
      }
    },

    async addCurrency(code) {
      const snap = await currenciesApi.addCurrency(code);
      this.codes = Array.isArray(snap?.codes) ? snap.codes.slice() : [];
      this.defaultCode = snap?.default_code || this.defaultCode;
      return snap;
    },

    async removeCurrency(code) {
      const snap = await currenciesApi.deleteCurrency(code);
      this.codes = Array.isArray(snap?.codes) ? snap.codes.slice() : [];
      this.defaultCode = snap?.default_code || this.defaultCode;
      // If the operator removed the last-used currency, fall back to
      // the default so the form stays in a valid state.
      if (this.lastUsed && !this.codes.includes(this.lastUsed)) {
        this.setLastUsed(this.defaultCode);
      }
      return snap;
    },

    setLastUsed(code) {
      const upper = typeof code === "string" ? code.toUpperCase() : null;
      this.lastUsed = upper;
      writeLastUsedToStorage(upper);
    },
  },
});
