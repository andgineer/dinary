-- Runtime 3D -> 2D projection candidates.
--
-- Returns sheet_mapping rows whose category_id either matches the
-- expense's category_id or is NULL (wildcard), in row_order ASC.
-- event_id and tag-set filtering happens in Python; this query keeps
-- the SQL small and supports the "first non-* wins per column"
-- resolver in ``duckdb_repo.logging_projection``.
SELECT
    m.row_order       AS row_order,
    m.category_id     AS category_id,
    m.event_id        AS event_id,
    m.sheet_category  AS sheet_category,
    m.sheet_group     AS sheet_group,
    COALESCE(
        (SELECT LIST(mt.tag_id ORDER BY mt.tag_id)
         FROM sheet_mapping_tags mt
         WHERE mt.mapping_row_order = m.row_order),
        []::INTEGER[]
    ) AS tag_ids
FROM sheet_mapping m
WHERE m.category_id IS NULL OR m.category_id = ?
ORDER BY m.row_order ASC
