/**
 * Category list management — two cascading dropdowns: group → category.
 */

import { fetchCategories } from "./api.js";

let _categories = [];
let _lastError = null;

export async function loadCategories() {
  _lastError = null;
  try {
    _categories = await fetchCategories();
  } catch (e) {
    _lastError = e;
    console.error("Failed to fetch categories:", e);
  }
  return _categories;
}

export function getLastError() {
  return _lastError;
}

export function getCategories() {
  return _categories;
}

export function populateGroupDropdown(selectEl) {
  selectEl.innerHTML = "";
  const seen = new Set();
  for (const cat of _categories) {
    const label = cat.group || "—";
    if (!seen.has(label)) {
      seen.add(label);
      const opt = document.createElement("option");
      opt.value = cat.group;
      opt.textContent = label;
      selectEl.appendChild(opt);
    }
  }
}

export function populateCategoryDropdown(selectEl, group) {
  selectEl.innerHTML = "";
  for (const cat of _categories) {
    if (cat.group === group) {
      const opt = document.createElement("option");
      opt.value = cat.name;
      opt.textContent = cat.name;
      selectEl.appendChild(opt);
    }
  }
}

export function selectDefaults(groupEl, categoryEl) {
  const emptyGroupExists = _categories.some((c) => c.group === "");
  if (emptyGroupExists) {
    groupEl.value = "";
    populateCategoryDropdown(categoryEl, "");
    const foodOption = Array.from(categoryEl.options).find(
      (o) => o.value === "еда&бытовые",
    );
    if (foodOption) categoryEl.value = "еда&бытовые";
  }
}
