SELECT
    ev.id,
    ev.name,
    ev.date_from,
    ev.date_to,
    (SELECT COALESCE(SUM(e.amount), 0) FROM expenses e WHERE e.event_id = ev.id)  AS total,
    CASE WHEN ev.date_to >= date('now') THEN 1 ELSE 0 END                          AS is_open
FROM events ev
WHERE ev.id = ?
