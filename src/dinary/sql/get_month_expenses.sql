SELECT e.id,
       e.datetime,
       e.amount,
       e.currency,
       e.category_id,
       e.beneficiary_id,
       e.event_id,
       e.store_id,
       e.comment,
       (SELECT list(tag_id ORDER BY tag_id)
        FROM expense_tags
        WHERE expense_id = e.id) AS tag_ids
FROM expenses e
WHERE YEAR(e.datetime) = ? AND MONTH(e.datetime) = ?
