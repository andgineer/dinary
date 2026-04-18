SELECT
    c.id             AS id,
    c.name           AS name,
    c.group_id       AS group_id,
    g.name           AS group_name,
    g.sort_order     AS group_sort_order
FROM categories c
JOIN category_groups g ON g.id = c.group_id
ORDER BY g.sort_order, c.name
