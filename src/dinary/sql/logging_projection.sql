SELECT
    m.id            AS id,
    m.sheet_category AS sheet_category,
    m.sheet_group   AS sheet_group,
    m.event_id      AS event_id,
    COALESCE(
        (SELECT LIST(mt.tag_id ORDER BY mt.tag_id)
         FROM logging_mapping_tags mt
         WHERE mt.mapping_id = m.id),
        []::INTEGER[]
    ) AS tag_ids
FROM logging_mapping m
WHERE m.category_id = ?
ORDER BY m.id ASC
