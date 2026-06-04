-- Top-5 category groups and tags by % change between the two most recent 3-month windows.
-- Noise filter: items with MAX(recent, prior) below 15% of the average for their kind
-- (groups vs tags compared separately so tag amounts aren't swamped by group amounts).
WITH period_amounts AS (
    SELECT
        'group'   AS kind,
        cg.id,
        cg.name,
        COALESCE(SUM(CASE WHEN date(e.datetime) >= date('now', '-3 months')
                          THEN e.amount ELSE 0 END), 0) AS recent,
        COALESCE(SUM(CASE WHEN date(e.datetime) <  date('now', '-3 months')
                           AND date(e.datetime) >= date('now', '-6 months')
                          THEN e.amount ELSE 0 END), 0) AS prior
    FROM category_groups cg
    JOIN categories c ON c.group_id = cg.id AND c.is_active = 1
    JOIN expenses e    ON e.category_id = c.id
    WHERE date(e.datetime) >= date('now', '-6 months')
      AND cg.is_active = 1
    GROUP BY cg.id, cg.name

    UNION ALL

    SELECT
        'tag',
        t.id,
        t.name,
        COALESCE(SUM(CASE WHEN date(e.datetime) >= date('now', '-3 months')
                          THEN e.amount ELSE 0 END), 0),
        COALESCE(SUM(CASE WHEN date(e.datetime) <  date('now', '-3 months')
                           AND date(e.datetime) >= date('now', '-6 months')
                          THEN e.amount ELSE 0 END), 0)
    FROM tags t
    JOIN expense_tags et ON et.tag_id = t.id
    JOIN expenses e      ON e.id = et.expense_id
    WHERE date(e.datetime) >= date('now', '-6 months')
      AND t.is_active = 1
    GROUP BY t.id, t.name
),
thresholds AS (
    SELECT kind,
           AVG(CASE WHEN recent > prior THEN recent ELSE prior END) * 0.15 AS min_amt
    FROM period_amounts
    WHERE recent > 0 OR prior > 0
    GROUP BY kind
)
SELECT
    p.kind,
    p.name,
    p.recent,
    p.prior,
    ROUND((p.recent - p.prior) * 100.0 / p.prior) AS pct_change,
    CASE WHEN p.recent >= p.prior THEN 'up' ELSE 'down' END AS direction
FROM period_amounts p
JOIN thresholds t ON t.kind = p.kind
WHERE p.prior > 0
  AND (CASE WHEN p.recent > p.prior THEN p.recent ELSE p.prior END) >= t.min_amt
ORDER BY ABS(p.recent - p.prior) * 1.0 / p.prior DESC
LIMIT 5
