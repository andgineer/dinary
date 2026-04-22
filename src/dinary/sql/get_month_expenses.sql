SELECT e.id,
       e.client_expense_id,
       e.datetime,
       e.amount,
       e.amount_original,
       e.currency_original,
       e.category_id,
       e.event_id,
       e.comment,
       e.sheet_category,
       e.sheet_group
FROM expenses e
WHERE CAST(strftime('%Y', e.datetime) AS INTEGER) = ?
  AND CAST(strftime('%m', e.datetime) AS INTEGER) = ?
