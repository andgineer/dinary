-- Parameters:
--   $1 : view config JSON
--        {"baskets":[{"name":"X","triggers":{"events":[3],"tags":[7],
--                                             "categories":[12],"category_groups":[2]}}],
--         "default_basket":"Other",
--         "scope":{"name":"Food","required":[<triggers>],"excluded":[<triggers>]}}
--        A missing trigger list is an empty list; "scope" is optional.
--   $2 : date_from as ISO date string, e.g. '2025-01-01'
--
-- An expense is in scope when it matches every "required" trigger set and none of the
-- "excluded" ones; out-of-scope expenses are dropped, not sent to the default basket.
--
-- Returns columns: basket_name, year_month, group_name, total_amount
-- ordered by basket_name, year_month, group_name

WITH
cfg AS (
    SELECT
        $1::JSON AS j,
        json_extract_string($1::JSON, '$.default_basket') AS default_basket,
        COALESCE(json_array_length($1::JSON, '$.baskets'), 0)::BIGINT AS n_baskets,
        COALESCE(json_array_length($1::JSON, '$.scope.required'), 0)::BIGINT AS n_required,
        COALESCE(json_array_length($1::JSON, '$.scope.excluded'), 0)::BIGINT AS n_excluded
),
raw_trigger_sets AS (
    SELECT
        'basket' AS kind,
        r.i AS idx,
        json_extract_string(j, '$.baskets[' || r.i::VARCHAR || '].name') AS basket_name,
        json_extract(j, '$.baskets[' || r.i::VARCHAR || '].triggers') AS triggers
    FROM cfg, range(n_baskets) AS r(i)
    UNION ALL
    SELECT
        'required',
        r.i,
        NULL,
        json_extract(j, '$.scope.required[' || r.i::VARCHAR || ']')
    FROM cfg, range(n_required) AS r(i)
    UNION ALL
    SELECT
        'excluded',
        r.i,
        NULL,
        json_extract(j, '$.scope.excluded[' || r.i::VARCHAR || ']')
    FROM cfg, range(n_excluded) AS r(i)
),
trigger_sets AS (
    SELECT
        kind,
        idx,
        basket_name,
        COALESCE(CAST(json_extract(triggers, '$.events') AS BIGINT[]), []) AS event_ids,
        COALESCE(CAST(json_extract(triggers, '$.tags') AS BIGINT[]), []) AS tag_ids,
        COALESCE(CAST(json_extract(triggers, '$.categories') AS BIGINT[]), []) AS category_ids,
        COALESCE(CAST(json_extract(triggers, '$.category_groups') AS BIGINT[]), [])
            AS group_ids
    FROM raw_trigger_sets
),
expense_tag_lists AS (
    SELECT expense_id, list(tag_id::BIGINT) AS tag_ids
    FROM ledger.expense_tags
    GROUP BY expense_id
),
period_expenses AS (
    SELECT
        e.id AS expense_id,
        e.amount,
        strftime(e.datetime::TIMESTAMP, '%Y-%m') AS year_month,
        e.event_id::BIGINT AS event_id,
        e.category_id::BIGINT AS category_id,
        c.group_id::BIGINT AS group_id,
        COALESCE(cg.name, 'Other') AS group_name,
        COALESCE(etl.tag_ids, []) AS tag_ids
    FROM ledger.expenses e
    JOIN ledger.categories c ON c.id = e.category_id
    LEFT JOIN ledger.category_groups cg ON cg.id = c.group_id
    LEFT JOIN expense_tag_lists etl ON etl.expense_id = e.id
    WHERE e.datetime::TIMESTAMP::DATE >= $2::DATE
),
matches AS (
    SELECT pe.expense_id, ts.kind, ts.idx, ts.basket_name
    FROM period_expenses pe
    JOIN trigger_sets ts ON (
        list_contains(ts.event_ids, pe.event_id)
        OR list_contains(ts.category_ids, pe.category_id)
        OR list_contains(ts.group_ids, pe.group_id)
        OR list_has_any(ts.tag_ids, pe.tag_ids)
    )
),
in_scope AS (
    SELECT pe.*
    FROM period_expenses pe
    WHERE NOT EXISTS (
        SELECT 1 FROM matches m
        WHERE m.expense_id = pe.expense_id AND m.kind = 'excluded'
    )
    AND (
        SELECT count(DISTINCT m.idx) FROM matches m
        WHERE m.expense_id = pe.expense_id AND m.kind = 'required'
    ) = (SELECT n_required FROM cfg)
),
first_match AS (
    SELECT expense_id, arg_min(basket_name, idx) AS basket_name
    FROM matches
    WHERE kind = 'basket'
    GROUP BY expense_id
)
SELECT
    COALESCE(fm.basket_name, (SELECT default_basket FROM cfg)) AS basket_name,
    s.year_month,
    s.group_name,
    CAST(SUM(s.amount) AS DOUBLE) AS total_amount
FROM in_scope s
LEFT JOIN first_match fm ON fm.expense_id = s.expense_id
GROUP BY 1, 2, 3
ORDER BY 1, 2, 3
