-- Returns ONE ROW with ONE column 'summary': a JSON object with three keys:
--   events, tags, category_groups

WITH cutoff AS (
    SELECT (CURRENT_DATE - INTERVAL '12 months')::DATE AS dt
),
event_totals AS (
    SELECT
        ev.id,
        ev.name,
        CAST(SUM(e.amount) AS DOUBLE) AS total_amount,
        STRFTIME(ev.date_from::DATE, '%Y-%m-%d') AS date_from,
        STRFTIME(ev.date_to::DATE, '%Y-%m-%d') AS date_to
    FROM ledger.expenses e
    JOIN ledger.events ev ON e.event_id = ev.id
    WHERE e.datetime::TIMESTAMP::DATE >= (SELECT dt FROM cutoff)
    GROUP BY ev.id, ev.name, ev.date_from, ev.date_to
),
tag_totals AS (
    SELECT
        t.id,
        t.name,
        COUNT(DISTINCT et.expense_id) AS expense_count,
        CAST(SUM(e.amount) AS DOUBLE) AS total_amount
    FROM ledger.expenses e
    JOIN ledger.expense_tags et ON et.expense_id = e.id
    JOIN ledger.tags t ON t.id = et.tag_id
    WHERE e.datetime::TIMESTAMP::DATE >= (SELECT dt FROM cutoff)
    GROUP BY t.id, t.name
),
group_totals AS (
    SELECT
        cg.id,
        cg.name,
        CAST(SUM(e.amount) AS DOUBLE) AS total_amount
    FROM ledger.expenses e
    JOIN ledger.categories c ON e.category_id = c.id
    JOIN ledger.category_groups cg ON c.group_id = cg.id
    WHERE cg.is_active = TRUE
      AND e.datetime::TIMESTAMP::DATE >= (SELECT dt FROM cutoff)
    GROUP BY cg.id, cg.name
),
events_json AS (
    SELECT COALESCE(
        to_json(list({
            'id':         id,
            'name':       name,
            'total_amount': total_amount,
            'date_from':  date_from,
            'date_to':    date_to
        } ORDER BY total_amount DESC)),
        '[]'
    ) AS val
    FROM event_totals
),
tags_json AS (
    SELECT COALESCE(
        to_json(list({
            'id':            id,
            'name':          name,
            'expense_count': expense_count,
            'total_amount':  total_amount
        } ORDER BY total_amount DESC)),
        '[]'
    ) AS val
    FROM tag_totals
),
groups_json AS (
    SELECT COALESCE(
        to_json(list({
            'id':           id,
            'name':         name,
            'total_amount': total_amount
        } ORDER BY total_amount DESC)),
        '[]'
    ) AS val
    FROM group_totals
)
SELECT json_object(
    'events',           (SELECT val FROM events_json),
    'tags',             (SELECT val FROM tags_json),
    'category_groups',  (SELECT val FROM groups_json)
) AS summary
