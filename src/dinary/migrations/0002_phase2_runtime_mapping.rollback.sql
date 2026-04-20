DROP TABLE IF EXISTS runtime_mapping_tags;
DROP TABLE IF EXISTS runtime_mapping;

CREATE TABLE logging_mapping (
    id             INTEGER PRIMARY KEY,
    category_id    INTEGER NOT NULL REFERENCES categories(id),
    event_id       INTEGER REFERENCES events(id),
    sheet_category TEXT NOT NULL,
    sheet_group    TEXT NOT NULL DEFAULT ''
);

CREATE TABLE logging_mapping_tags (
    mapping_id INTEGER NOT NULL REFERENCES logging_mapping(id),
    tag_id     INTEGER NOT NULL REFERENCES tags(id),
    PRIMARY KEY (mapping_id, tag_id)
);
