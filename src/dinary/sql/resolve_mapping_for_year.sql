SELECT category_id, beneficiary_id, event_id, tag_ids
FROM config.source_type_mapping
WHERE source_type = ? AND source_envelope = ? AND year IN (?, 0)
ORDER BY year DESC
LIMIT 1
