SELECT sheet_category, sheet_group
FROM config.sheet_category_mapping
WHERE sheet_group = ? AND category_id = ?
