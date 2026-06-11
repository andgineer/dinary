SELECT c.id AS id, c.code AS code, c.name AS name, c.is_active AS is_active,
       c.is_hidden AS is_hidden
FROM categories c
WHERE NOT c.is_retired AND c.name LIKE '%' || ? || '%'
ORDER BY c.is_active DESC, c.name
