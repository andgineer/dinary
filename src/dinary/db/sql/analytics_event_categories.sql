SELECT
    c.id,
    c.name,
    g.name         AS group_name,
    SUM(e.amount)  AS total
FROM expenses e
JOIN categories c ON c.id = e.category_id
LEFT JOIN category_groups g ON g.id = c.group_id
WHERE e.event_id = ?
GROUP BY c.id, c.name, g.name
ORDER BY total DESC, c.name
