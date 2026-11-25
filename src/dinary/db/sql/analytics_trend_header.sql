-- One basket (category group or active tag) over the two windows of analytics_auto_trends.sql.
-- No row when the basket does not exist.
WITH basket AS (
    SELECT name FROM category_groups WHERE :kind = 'group' AND id = :basket_id
    UNION ALL
    SELECT name FROM tags WHERE :kind = 'tag' AND id = :basket_id AND is_active = 1
),
totals AS (
    SELECT
        COALESCE(SUM(CASE WHEN date(e.datetime) >= date('now', '-3 months')
                          THEN e.amount ELSE 0 END), 0) AS recent,
        COALESCE(SUM(CASE WHEN date(e.datetime) <  date('now', '-3 months')
                           AND date(e.datetime) >= date('now', '-6 months')
                          THEN e.amount ELSE 0 END), 0) AS prior,
        COALESCE(SUM(CASE WHEN e.receipt_id IS NOT NULL
                           AND date(e.datetime) >= date('now', '-3 months')
                          THEN e.amount ELSE 0 END), 0) AS receipt_recent,
        COALESCE(SUM(CASE WHEN e.receipt_id IS NOT NULL
                           AND date(e.datetime) <  date('now', '-3 months')
                           AND date(e.datetime) >= date('now', '-6 months')
                          THEN e.amount ELSE 0 END), 0) AS receipt_prior
    FROM expenses e
    JOIN categories c ON c.id = e.category_id
    WHERE date(e.datetime) >= date('now', '-6 months')
      AND (
            (:kind = 'group' AND c.group_id = :basket_id)
         OR (:kind = 'tag'   AND EXISTS (SELECT 1 FROM expense_tags et
                                         WHERE et.expense_id = e.id AND et.tag_id = :basket_id))
          )
)
SELECT
    b.name,
    t.recent,
    t.prior,
    t.receipt_recent,
    t.receipt_prior,
    CASE WHEN t.prior > 0 THEN ROUND((t.recent - t.prior) * 100.0 / t.prior) END AS pct_change,
    CASE WHEN t.recent >= t.prior THEN 'up' ELSE 'down' END AS direction
FROM basket b
CROSS JOIN totals t
