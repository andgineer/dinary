SELECT e.id,
       e.datetime,
       e.amount,
       e.amount_original,
       e.currency_original,
       e.category_id,
       e.beneficiary_id,
       e.event_id,
       e.sphere_of_life_id,
       e.comment,
       e.source_type,
       e.source_envelope
FROM expenses e
WHERE YEAR(e.datetime) = ? AND MONTH(e.datetime) = ?
