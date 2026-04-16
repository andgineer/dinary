INSERT INTO expenses (id, datetime, amount, currency, category_id,
                      beneficiary_id, event_id, comment)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT DO NOTHING
RETURNING id
