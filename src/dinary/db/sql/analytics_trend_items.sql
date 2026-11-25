SELECT
    COALESCE(NULLIF(ri.name_normalized, ''), ri.name_raw) AS key,
    COALESCE(NULLIF(ri.name_normalized, ''), ri.name_raw) AS label,
    NULL AS sublabel,
    COALESCE(SUM(CASE WHEN date(e.datetime) >= date('now', '-3 months')
                      THEN e.amount ELSE 0 END), 0) AS recent,
    COALESCE(SUM(CASE WHEN date(e.datetime) <  date('now', '-3 months')
                       AND date(e.datetime) >= date('now', '-6 months')
                      THEN e.amount ELSE 0 END), 0) AS prior
FROM expenses e
JOIN categories c ON c.id = e.category_id
-- Sums e.amount, not ri.total_price, to stay in the accounting currency.
JOIN receipt_items ri ON ri.expense_id = e.id
WHERE date(e.datetime) >= date('now', '-6 months')
  AND (
        (:kind = 'group' AND c.group_id = :basket_id)
     OR (:kind = 'tag'   AND EXISTS (SELECT 1 FROM expense_tags et
                                     WHERE et.expense_id = e.id AND et.tag_id = :basket_id))
      )
GROUP BY COALESCE(NULLIF(ri.name_normalized, ''), ri.name_raw)
ORDER BY ABS(recent - prior) DESC, label, key
