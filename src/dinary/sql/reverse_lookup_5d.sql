SELECT source_type, source_envelope, tag_ids
FROM config.source_type_mapping
WHERE year = 0
  AND category_id = ?
  AND beneficiary_id IS NOT DISTINCT FROM ?
  AND event_id IS NOT DISTINCT FROM ?
  AND source_envelope != 'путешествия'
