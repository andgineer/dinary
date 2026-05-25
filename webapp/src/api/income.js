import { apiRequest } from "./_request.js";

export function listIncomes({ page = 1, pageSize = 20 } = {}) {
  return apiRequest(`/api/incomes?page=${page}&page_size=${pageSize}`);
}

export function createIncome({ year, month, income_date, amount_original, currency_original, comment }) {
  return apiRequest("/api/incomes", {
    method: "POST",
    body: { year, month, income_date, amount_original, currency_original, comment },
  });
}

export function updateIncome(id, { year, month, amount_original, currency_original, income_date, comment }) {
  return apiRequest(`/api/incomes/${id}`, {
    method: "PATCH",
    body: { year, month, amount_original, currency_original, income_date, comment },
  });
}

export function deleteIncome(id) {
  return apiRequest(`/api/incomes/${id}`, { method: "DELETE" });
}
