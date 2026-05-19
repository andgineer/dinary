import { apiRequest } from "./_request.js";

export function correctCategory(expenseId, categoryId, scope = "all") {
  return apiRequest(`/api/expenses/${expenseId}/category`, {
    method: "PATCH",
    body: { category_id: categoryId, scope },
  });
}

export function editExpense(expenseId, payload) {
  return apiRequest(`/api/expenses/${expenseId}`, {
    method: "PATCH",
    body: payload,
  });
}
