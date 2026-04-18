-- Returns all sheet_mapping rows for (year, category_id) with their tag-id arrays.
-- Application code picks: exact (event_id, tag-set) match first; otherwise the
-- first row by id ASC.
SELECT
    m.id            AS id,
    m.sheet_category AS sheet_category,
    m.sheet_group   AS sheet_group,
    m.event_id      AS event_id,
    COALESCE(
        (SELECT LIST(mt.tag_id ORDER BY mt.tag_id)
         FROM config.sheet_mapping_tags mt
         WHERE mt.mapping_id = m.id),
        []::INTEGER[]
    ) AS tag_ids
FROM config.sheet_mapping m
WHERE m.year = ? AND m.category_id = ?
ORDER BY m.id ASC
