SELECT sheet_category, sheet_group
FROM config.sheet_category_mapping
WHERE year = 0 AND sheet_group = ? AND category_id = ?
