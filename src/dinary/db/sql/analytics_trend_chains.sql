SELECT
    sc.id AS key,
    sc.name AS label,
    NULL AS sublabel,
    COALESCE(SUM(CASE WHEN date(e.datetime) >= date('now', '-3 months')
                      THEN e.amount ELSE 0 END), 0) AS recent,
    COALESCE(SUM(CASE WHEN date(e.datetime) <  date('now', '-3 months')
                       AND date(e.datetime) >= date('now', '-6 months')
                      THEN e.amount ELSE 0 END), 0) AS prior
FROM expenses e
JOIN categories c ON c.id = e.category_id
JOIN stores s ON s.id = e.store_id
JOIN shop_chains sc ON sc.id = s.chain_id
WHERE date(e.datetime) >= date('now', '-6 months')
  AND (
        (:kind = 'group' AND c.group_id = :basket_id)
     OR (:kind = 'tag'   AND EXISTS (SELECT 1 FROM expense_tags et
                                     WHERE et.expense_id = e.id AND et.tag_id = :basket_id))
      )
  AND e.receipt_id IS NOT NULL
GROUP BY sc.id, sc.name
ORDER BY ABS(recent - prior) DESC, label, key
