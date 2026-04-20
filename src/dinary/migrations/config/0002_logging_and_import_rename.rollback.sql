-- Reverse of 0002_logging_and_import_rename.sql.

DROP TABLE IF EXISTS logging_mapping_tags;
DROP TABLE IF EXISTS logging_mapping;

CREATE TABLE sheet_mapping (
    id             INTEGER PRIMARY KEY,
    year           INTEGER NOT NULL DEFAULT 0,
    sheet_category TEXT NOT NULL,
    sheet_group    TEXT NOT NULL DEFAULT '',
    category_id    INTEGER NOT NULL REFERENCES categories(id),
    event_id       INTEGER REFERENCES events(id),
    UNIQUE (year, sheet_category, sheet_group)
);
INSERT INTO sheet_mapping SELECT * FROM import_mapping;

CREATE TABLE sheet_mapping_tags (
    mapping_id INTEGER NOT NULL REFERENCES sheet_mapping(id),
    tag_id     INTEGER NOT NULL REFERENCES tags(id),
    PRIMARY KEY (mapping_id, tag_id)
);
INSERT INTO sheet_mapping_tags SELECT * FROM import_mapping_tags;

DROP TABLE import_mapping_tags;
DROP TABLE import_mapping;

ALTER TABLE import_sources RENAME TO sheet_import_sources;
