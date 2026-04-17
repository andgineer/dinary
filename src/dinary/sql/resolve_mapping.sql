SELECT category_id, beneficiary_id, event_id, sphere_of_life_id
FROM config.source_type_mapping
WHERE source_type = ? AND source_envelope = ? AND year = 0
