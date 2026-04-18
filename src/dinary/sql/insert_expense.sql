INSERT INTO expenses (id, datetime, amount, amount_original, currency_original,
                      category_id, event_id, comment,
                      sheet_category, sheet_group)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT DO NOTHING
RETURNING id
