CREATE TABLE sheet_category_mapping_old (
    sheet_category  TEXT NOT NULL,
    sheet_group     TEXT NOT NULL DEFAULT '',
    category_id     INTEGER NOT NULL REFERENCES categories(id),
    beneficiary_id  INTEGER REFERENCES family_members(id),
    event_id        INTEGER REFERENCES events(id),
    store_id        INTEGER REFERENCES stores(id),
    tag_ids         INTEGER[],
    PRIMARY KEY (sheet_category, sheet_group)
);

INSERT INTO sheet_category_mapping_old
    SELECT sheet_category, sheet_group, category_id,
           beneficiary_id, event_id, store_id, tag_ids
    FROM sheet_category_mapping
    WHERE year = 0;

DROP TABLE sheet_category_mapping;

ALTER TABLE sheet_category_mapping_old RENAME TO sheet_category_mapping;

DROP TABLE IF EXISTS sheet_import_sources;
