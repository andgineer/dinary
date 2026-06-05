-- Parameters:
--   $1 : basket config JSON  {"baskets":[{"name":"X","triggers":{"events":[3],"tags":[7]}}],"default_basket":"Other"}
--   $2 : date_from as ISO date string, e.g. '2025-01-01'
--
-- Returns columns: basket_name, year_month, group_name, total_amount
-- ordered by basket_name, year_month, group_name

WITH
basket_cfg AS (
    SELECT
        $1::JSON AS cfg,
        json_array_length($1::JSON, '$.baskets') AS n_baskets,
        json_extract_string($1::JSON, '$.default_basket') AS default_basket
),
basket_index AS (
    SELECT
        gs.idx,
        json_extract_string(cfg, '$.baskets[' || gs.idx::VARCHAR || '].name') AS basket_name,
        CAST(
            json_extract(cfg, '$.baskets[' || gs.idx::VARCHAR || '].triggers.events')
            AS INTEGER[]
        ) AS event_ids,
        CAST(
            json_extract(cfg, '$.baskets[' || gs.idx::VARCHAR || '].triggers.tags')
            AS INTEGER[]
        ) AS tag_ids
    FROM basket_cfg,
         generate_series(0::BIGINT, (n_baskets - 1)::BIGINT) AS gs(idx)
),
expense_basket AS (
    SELECT
        e.id AS expense_id,
        e.amount,
        strftime(e.datetime::TIMESTAMP, '%Y-%m') AS year_month,
        COALESCE(c.group_id::VARCHAR, 'unknown') AS group_id,
        COALESCE(cg.name, 'Other') AS group_name,
        (
            SELECT bi.basket_name
            FROM basket_index bi
            WHERE (
                e.event_id IS NOT NULL
                AND list_contains(bi.event_ids, e.event_id)
            ) OR EXISTS (
                SELECT 1
                FROM ledger.expense_tags et
                WHERE et.expense_id = e.id
                  AND list_contains(bi.tag_ids, et.tag_id)
            )
            ORDER BY bi.idx ASC
            LIMIT 1
        ) AS matched_basket
    FROM ledger.expenses e
    JOIN ledger.categories c ON c.id = e.category_id
    LEFT JOIN ledger.category_groups cg ON cg.id = c.group_id
    WHERE e.datetime::TIMESTAMP::DATE >= $2::DATE
),
expense_with_basket AS (
    SELECT
        expense_id,
        amount,
        year_month,
        group_name,
        COALESCE(matched_basket, (SELECT default_basket FROM basket_cfg)) AS basket_name
    FROM expense_basket
)
SELECT
    basket_name,
    year_month,
    group_name,
    CAST(SUM(amount) AS DOUBLE) AS total_amount
FROM expense_with_basket
GROUP BY basket_name, year_month, group_name
ORDER BY basket_name, year_month, group_name
