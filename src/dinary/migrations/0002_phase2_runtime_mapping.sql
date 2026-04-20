-- Phase 2: runtime 3D mapping.
--
-- Drop the auto-generated logging_mapping / logging_mapping_tags tables
-- (they produced stale capitalisations and cross-category aliases).
-- Replace with runtime_mapping / runtime_mapping_tags, which mirror a
-- hand-curated `map` tab in the logging spreadsheet. Each runtime row
-- carries a category filter, a glob event pattern, required tag set
-- and a target (sheet_category, sheet_group) pair, evaluated in
-- first-match-wins order by row_order.
--
-- events.date_from / date_to / is_active already exist in the initial
-- schema; no alterations to events are needed.

DROP TABLE IF EXISTS logging_mapping_tags;
DROP TABLE IF EXISTS logging_mapping;

CREATE TABLE runtime_mapping (
    row_order      INTEGER PRIMARY KEY,
    category_id    INTEGER NOT NULL REFERENCES categories(id),
    event_pattern  TEXT NOT NULL,
    sheet_category TEXT NOT NULL,
    sheet_group    TEXT NOT NULL DEFAULT ''
);

CREATE TABLE runtime_mapping_tags (
    mapping_row_order INTEGER NOT NULL REFERENCES runtime_mapping(row_order),
    tag_id            INTEGER NOT NULL REFERENCES tags(id),
    PRIMARY KEY (mapping_row_order, tag_id)
);
