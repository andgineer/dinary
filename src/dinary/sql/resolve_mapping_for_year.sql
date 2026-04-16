SELECT category_id, beneficiary_id, event_id, store_id, tag_ids
FROM config.sheet_category_mapping
WHERE sheet_category = ? AND sheet_group = ? AND year IN (?, 0)
ORDER BY year DESC
LIMIT 1
