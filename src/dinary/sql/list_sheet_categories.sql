SELECT m.source_type, m.source_envelope
FROM source_type_mapping m
WHERE m.year = 0
ORDER BY m.source_envelope, m.source_type
