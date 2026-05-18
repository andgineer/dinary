-- Runtime 3D -> 2D projection candidates.
--
-- Returns sheet_mapping rows whose category_id either matches the
-- expense's category_id or is NULL (wildcard), in row_order ASC.
-- event_id and tag-set filtering happens in Python; this query keeps
-- the SQL small and supports the "first non-* wins per column"
-- resolver in ``ledger_repo.logging_projection``.
--
-- ``tag_ids_json`` comes back as a JSON-encoded string
-- (``"[1,2,3]"``) built by ``json_group_array``. Python decodes it
-- in ``logging_projection`` to avoid a per-row extra query. The
-- column alias is named with the ``_json`` suffix to keep the
-- raw-string shape obvious at call sites via
-- ``LoggingProjectionCandidateRow.tag_ids_json``.
SELECT
    m.row_order       AS row_order,
    m.category_id     AS category_id,
    m.event_id        AS event_id,
    m.sheet_category  AS sheet_category,
    m.sheet_group     AS sheet_group,
    COALESCE(
        (SELECT json_group_array(tag_id)
         FROM (
             SELECT mt.tag_id
             FROM sheet_mapping_tags mt
             WHERE mt.mapping_row_order = m.row_order
             ORDER BY mt.tag_id
         )),
        '[]'
    ) AS tag_ids_json
FROM sheet_mapping m
WHERE m.category_id IS NULL OR m.category_id = ?
ORDER BY m.row_order ASC
