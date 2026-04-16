SELECT m.sheet_category, m.sheet_group
FROM sheet_category_mapping m
WHERE m.year = 0
ORDER BY m.sheet_group, m.sheet_category
