import { apiRequest } from "./_request.js";

export function correctCategory(expenseId, categoryId) {
  return apiRequest(`/api/expenses/${expenseId}/category`, {
    method: "PATCH",
    body: { category_id: categoryId },
  });
}
