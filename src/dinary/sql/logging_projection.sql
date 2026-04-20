-- Runtime 3D -> 2D projection candidates for a single category.
--
-- Returns, in row_order ASC, all runtime_mapping rows for the given
-- category_id along with their required tag set (possibly empty).
-- ``event_pattern`` is an fnmatch-style glob (empty string = match
-- any event or no event); the caller resolves the expense's event
-- name and tag set and picks the first row that matches.
SELECT
    m.row_order       AS row_order,
    m.event_pattern   AS event_pattern,
    m.sheet_category  AS sheet_category,
    m.sheet_group     AS sheet_group,
    COALESCE(
        (SELECT LIST(mt.tag_id ORDER BY mt.tag_id)
         FROM runtime_mapping_tags mt
         WHERE mt.mapping_row_order = m.row_order),
        []::INTEGER[]
    ) AS tag_ids
FROM runtime_mapping m
WHERE m.category_id = ?
ORDER BY m.row_order ASC
