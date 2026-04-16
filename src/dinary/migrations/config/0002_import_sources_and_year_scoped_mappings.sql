-- Year-scoped import source metadata: which spreadsheet/worksheet to use per year
CREATE TABLE IF NOT EXISTS sheet_import_sources (
    year            INTEGER PRIMARY KEY,
    spreadsheet_id  TEXT NOT NULL,
    worksheet_name  TEXT NOT NULL DEFAULT '',
    layout_key      TEXT NOT NULL DEFAULT 'default',
    notes           TEXT
);

-- Extend sheet_category_mapping with a year dimension.
-- year=0 means "default for any year" (existing rows).
-- year=YYYY means "override for that specific year".
-- Resolve logic: prefer exact year match, fall back to year=0.
CREATE TABLE sheet_category_mapping_v2 (
    year            INTEGER NOT NULL DEFAULT 0,
    sheet_category  TEXT NOT NULL,
    sheet_group     TEXT NOT NULL DEFAULT '',
    category_id     INTEGER NOT NULL REFERENCES categories(id),
    beneficiary_id  INTEGER REFERENCES family_members(id),
    event_id        INTEGER REFERENCES events(id),
    store_id        INTEGER REFERENCES stores(id),
    tag_ids         INTEGER[],
    PRIMARY KEY (year, sheet_category, sheet_group)
);

INSERT INTO sheet_category_mapping_v2
    SELECT 0, sheet_category, sheet_group, category_id,
           beneficiary_id, event_id, store_id, tag_ids
    FROM sheet_category_mapping;

DROP TABLE sheet_category_mapping;

ALTER TABLE sheet_category_mapping_v2 RENAME TO sheet_category_mapping;
