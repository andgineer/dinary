SELECT id, amount, amount_original, currency_original, category_id,
       event_id, comment, datetime,
       sheet_category, sheet_group
FROM expenses WHERE client_expense_id = ?
