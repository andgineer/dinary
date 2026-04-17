SELECT source_type, source_envelope, sphere_of_life_id
FROM config.source_type_mapping
WHERE year IN (?, 0)
  AND category_id = ?
  AND beneficiary_id IS NOT DISTINCT FROM ?
  AND event_id IS NOT DISTINCT FROM ?
  AND source_envelope != 'путешествия'
ORDER BY year DESC, source_type, source_envelope
