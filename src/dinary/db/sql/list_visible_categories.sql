SELECT c.id AS id, c.code AS code, c.name AS name,
       c.group_id AS group_id, g.name AS group_name,
       g.sort_order AS group_sort_order
FROM categories c
JOIN category_groups g ON g.id = c.group_id
LEFT JOIN (SELECT DISTINCT category_id FROM expenses) u
       ON u.category_id = c.id
WHERE NOT c.is_retired AND NOT c.is_hidden
      AND (c.is_active OR u.category_id IS NOT NULL)
ORDER BY g.sort_order, c.name
