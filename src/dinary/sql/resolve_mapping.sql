SELECT id, category_id, event_id
FROM config.sheet_mapping
WHERE sheet_category = ? AND sheet_group = ? AND year = 0
