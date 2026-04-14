/**
 * Category list management — fetches from backend, populates dropdown,
 * auto-fills group when a category is selected.
 */

import { fetchCategories } from "./api.js";

let _categories = [];

export async function loadCategories() {
  try {
    _categories = await fetchCategories();
  } catch {
    console.warn("Failed to fetch categories, using cached list");
  }
  return _categories;
}

export function getCategories() {
  return _categories;
}

export function groupFor(categoryName) {
  const cat = _categories.find((c) => c.name === categoryName);
  return cat ? cat.group : "";
}

export function populateDropdown(selectEl) {
  selectEl.innerHTML = '<option value="">— select —</option>';

  const groups = {};
  for (const cat of _categories) {
    if (!groups[cat.group]) groups[cat.group] = [];
    groups[cat.group].push(cat.name);
  }

  for (const [group, cats] of Object.entries(groups)) {
    const optgroup = document.createElement("optgroup");
    optgroup.label = group;
    for (const name of cats) {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      optgroup.appendChild(opt);
    }
    selectEl.appendChild(optgroup);
  }
}
