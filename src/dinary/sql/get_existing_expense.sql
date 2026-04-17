SELECT amount, amount_original, currency_original, category_id, beneficiary_id,
       event_id, sphere_of_life_id, comment, datetime,
       source_type, source_envelope
FROM expenses WHERE id = ?
