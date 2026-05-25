import { apiRequest } from "./_request.js";

export function listIncomes({ page = 1, pageSize = 20 } = {}) {
  return apiRequest(`/api/incomes?page=${page}&page_size=${pageSize}`);
}

export function createIncome({ year, month, amount_original, currency_original }) {
  return apiRequest("/api/incomes", {
    method: "POST",
    body: { year, month, amount_original, currency_original },
  });
}

export function updateIncome(year, month, { amount_original, currency_original }) {
  return apiRequest(`/api/incomes/${year}/${month}`, {
    method: "PATCH",
    body: { amount_original, currency_original },
  });
}

export function deleteIncome(year, month) {
  return apiRequest(`/api/incomes/${year}/${month}`, { method: "DELETE" });
}
