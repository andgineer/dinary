-- substr, not date(): date() converts the stored offset to UTC and can shift the day.
SELECT
    substr(e.datetime, 1, 10)  AS day,
    SUM(e.amount)              AS total
FROM expenses e
WHERE e.event_id = ?
GROUP BY day
ORDER BY day DESC
LIMIT ?
