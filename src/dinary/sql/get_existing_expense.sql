SELECT amount, currency, category_id, beneficiary_id,
       event_id, comment, datetime
FROM expenses WHERE id = ?
