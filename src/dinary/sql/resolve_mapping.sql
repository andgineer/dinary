SELECT id, category_id, event_id
FROM config.import_mapping
WHERE sheet_category = ? AND sheet_group = ? AND year = 0
