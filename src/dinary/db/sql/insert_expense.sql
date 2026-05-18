INSERT INTO expenses (client_expense_id, datetime, amount, amount_original, currency_original,
                      category_id, event_id, comment,
                      sheet_category, sheet_group)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (client_expense_id) DO NOTHING
RETURNING id
