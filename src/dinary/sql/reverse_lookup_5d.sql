SELECT sheet_category, sheet_group, tag_ids
FROM config.sheet_category_mapping
WHERE year = 0
  AND category_id = ?
  AND beneficiary_id IS NOT DISTINCT FROM ?
  AND event_id IS NOT DISTINCT FROM ?
  AND store_id IS NOT DISTINCT FROM ?
