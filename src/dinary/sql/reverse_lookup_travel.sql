SELECT source_type, source_envelope
FROM config.source_type_mapping
WHERE year = 0 AND source_envelope = ? AND category_id = ?
