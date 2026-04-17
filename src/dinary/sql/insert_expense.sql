INSERT INTO expenses (id, datetime, amount, amount_original, currency_original,
                      category_id, beneficiary_id, event_id, sphere_of_life_id,
                      comment, source_type, source_envelope)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT DO NOTHING
RETURNING id
