-- Returns ONE ROW with ONE column 'summary': a JSON object with five keys:
--   events, tags, category_groups (each with its categories), stores, uncovered
-- 'uncovered' is the spend with neither an event nor a tag: no event or tag basket can
-- reach it, only a category or category-group basket can.

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
category_totals AS (
    SELECT
        cg.id AS group_id,
        cg.name AS group_name,
        c.id,
        c.name,
        CAST(SUM(e.amount) AS DOUBLE) AS total_amount
    FROM ledger.expenses e
    JOIN ledger.categories c ON e.category_id = c.id
    JOIN ledger.category_groups cg ON c.group_id = cg.id
    WHERE cg.is_active = TRUE
      AND e.datetime::TIMESTAMP::DATE >= (SELECT dt FROM cutoff)
    GROUP BY cg.id, cg.name, c.id, c.name
),
group_totals AS (
    SELECT
        group_id AS id,
        group_name AS name,
        CAST(SUM(total_amount) AS DOUBLE) AS total_amount,
        list({
            'id':           id,
            'name':         name,
            'total_amount': total_amount
        } ORDER BY total_amount DESC, id) AS categories
    FROM category_totals
    GROUP BY group_id, group_name
),
uncovered_totals AS (
    SELECT
        CAST(COALESCE(SUM(e.amount), 0) AS DOUBLE) AS total_spend,
        CAST(COALESCE(SUM(e.amount) FILTER (
            WHERE e.event_id IS NULL
              AND NOT EXISTS (SELECT 1 FROM ledger.expense_tags et WHERE et.expense_id = e.id)
        ), 0) AS DOUBLE) AS total_amount
    FROM ledger.expenses e
    WHERE e.datetime::TIMESTAMP::DATE >= (SELECT dt FROM cutoff)
),
store_totals AS (
    SELECT
        s.id,
        s.name,
        sc.name AS chain_name,
        COUNT(*) AS expense_count,
        CAST(SUM(e.amount) AS DOUBLE) AS total_amount
    FROM ledger.expenses e
    JOIN ledger.stores s ON e.store_id = s.id
    LEFT JOIN ledger.shop_chains sc ON s.chain_id = sc.id
    WHERE e.datetime::TIMESTAMP::DATE >= (SELECT dt FROM cutoff)
    GROUP BY s.id, s.name, sc.name
    ORDER BY total_amount DESC, s.id
    LIMIT 20
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
            'total_amount': total_amount,
            'categories':   categories
        } ORDER BY total_amount DESC)),
        '[]'
    ) AS val
    FROM group_totals
),
stores_json AS (
    SELECT COALESCE(
        to_json(list({
            'id':            id,
            'name':          name,
            'chain_name':    chain_name,
            'expense_count': expense_count,
            'total_amount':  total_amount
        } ORDER BY total_amount DESC, id)),
        '[]'
    ) AS val
    FROM store_totals
)
SELECT json_object(
    'events',           (SELECT val FROM events_json),
    'tags',             (SELECT val FROM tags_json),
    'category_groups',  (SELECT val FROM groups_json),
    'stores',           (SELECT val FROM stores_json),
    'uncovered',        (
        SELECT json_object(
            'total_amount', total_amount,
            'share', CASE WHEN total_spend > 0 THEN round(total_amount / total_spend, 4) ELSE 0 END
        )
        FROM uncovered_totals
    )
) AS summary
