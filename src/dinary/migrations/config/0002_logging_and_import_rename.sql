-- Single follow-up migration to 0001 covering two related schema changes:
--
-- 1. Rename "sheet_*" tables that are import-only to make their purpose
--    obvious now that runtime sheet logging owns its own tables:
--      sheet_import_sources -> import_sources
--      sheet_mapping        -> import_mapping
--      sheet_mapping_tags   -> import_mapping_tags
--
--    `sheet_import_sources` has no FK dependents, so a plain
--    ALTER TABLE ... RENAME works.
--
--    `sheet_mapping` is referenced by `sheet_mapping_tags(mapping_id)`,
--    and DuckDB rejects ALTER TABLE RENAME on a table with incoming
--    FKs (DependencyException). We could drop the child first, rename
--    the parent in place, and rebuild the child — but the natural
--    "stash rows in a TEMP table" step does not survive yoyo's
--    statement execution (TEMP scope is lost between statements).
--    Recreating both tables under their new names and copying the
--    rows over is the simpler all-in-one path: no intermediate FK
--    juggling, no TEMP scoping surprises.
--
-- 2. Add `logging_mapping` (+ `logging_mapping_tags`) for the year-
--    agnostic 3D->2D projection used by sheet logging. Pre-fill from
--    the year=0 generic rows of the just-renamed `import_mapping` so
--    upgrades from a populated DB get a working logging projection
--    without requiring a follow-up `inv import-config` call. On a fresh
--    install both tables are empty here and `import-config` populates
--    them later (see `seed_config._sync_logging_mapping_from_year_zero`).

-- Section 1: rename import tables ------------------------------------------

ALTER TABLE sheet_import_sources RENAME TO import_sources;

CREATE TABLE import_mapping (
    id             INTEGER PRIMARY KEY,
    year           INTEGER NOT NULL DEFAULT 0,
    sheet_category TEXT NOT NULL,
    sheet_group    TEXT NOT NULL DEFAULT '',
    category_id    INTEGER NOT NULL REFERENCES categories(id),
    event_id       INTEGER REFERENCES events(id),
    UNIQUE (year, sheet_category, sheet_group)
);
INSERT INTO import_mapping SELECT * FROM sheet_mapping;

CREATE TABLE import_mapping_tags (
    mapping_id INTEGER NOT NULL REFERENCES import_mapping(id),
    tag_id     INTEGER NOT NULL REFERENCES tags(id),
    PRIMARY KEY (mapping_id, tag_id)
);
INSERT INTO import_mapping_tags SELECT * FROM sheet_mapping_tags;

-- Drop child first to release the FK, then drop the parent.
DROP TABLE sheet_mapping_tags;
DROP TABLE sheet_mapping;

-- Section 2: logging mapping (3D -> 2D, year-agnostic) ---------------------

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

INSERT INTO logging_mapping (id, category_id, event_id, sheet_category, sheet_group)
SELECT
    ROW_NUMBER() OVER (ORDER BY m.id) AS id,
    m.category_id,
    m.event_id,
    m.sheet_category,
    m.sheet_group
FROM import_mapping m
WHERE m.year = 0
ORDER BY m.id;

INSERT INTO logging_mapping_tags (mapping_id, tag_id)
SELECT lm.id, imt.tag_id
FROM logging_mapping lm
JOIN import_mapping im
    ON im.category_id = lm.category_id
   AND COALESCE(im.event_id, -1) = COALESCE(lm.event_id, -1)
   AND im.sheet_category = lm.sheet_category
   AND im.sheet_group = lm.sheet_group
   AND im.year = 0
JOIN import_mapping_tags imt
    ON imt.mapping_id = im.id;
