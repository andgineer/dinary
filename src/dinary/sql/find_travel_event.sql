SELECT id AS event_id
FROM events
WHERE name = ? AND date_from <= ? AND date_to >= ?
