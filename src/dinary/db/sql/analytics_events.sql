SELECT
    ev.id,
    ev.name,
    ev.date_from,
    ev.date_to,
    COALESCE(SUM(e.amount), 0)                             AS total,
    CASE WHEN ev.date_to >= date('now') THEN 1 ELSE 0 END  AS is_open
FROM events ev
LEFT JOIN expenses e ON e.event_id = ev.id
WHERE ev.date_from >= date('now', '-12 months')
GROUP BY ev.id, ev.name, ev.date_from, ev.date_to
ORDER BY ev.date_from DESC
