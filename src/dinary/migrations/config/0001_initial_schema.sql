CREATE TABLE categories (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE family_members (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE events (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL,
    date_from DATE NOT NULL,
    date_to   DATE NOT NULL,
    is_active BOOLEAN DEFAULT true,
    comment   TEXT
);

CREATE TABLE event_members (
    event_id  INTEGER NOT NULL REFERENCES events(id),
    member_id INTEGER NOT NULL REFERENCES family_members(id),
    PRIMARY KEY (event_id, member_id)
);

CREATE TABLE spheres_of_life (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE source_type_mapping (
    year               INTEGER NOT NULL DEFAULT 0,
    source_type        TEXT NOT NULL,
    source_envelope    TEXT NOT NULL DEFAULT '',
    category_id        INTEGER NOT NULL REFERENCES categories(id),
    beneficiary_id     INTEGER REFERENCES family_members(id),
    event_id           INTEGER REFERENCES events(id),
    sphere_of_life_id  INTEGER REFERENCES spheres_of_life(id),
    PRIMARY KEY (year, source_type, source_envelope)
);

CREATE TABLE sheet_import_sources (
    year           INTEGER PRIMARY KEY,
    spreadsheet_id TEXT NOT NULL,
    worksheet_name TEXT NOT NULL DEFAULT '',
    layout_key     TEXT NOT NULL DEFAULT 'default',
    notes          TEXT
);

CREATE TABLE category_taxonomies (
    id    INTEGER PRIMARY KEY,
    key   TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL
);

CREATE TABLE category_taxonomy_nodes (
    id          INTEGER PRIMARY KEY,
    taxonomy_id INTEGER NOT NULL REFERENCES category_taxonomies(id),
    key         TEXT NOT NULL,
    title       TEXT NOT NULL,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    UNIQUE(taxonomy_id, key)
);

CREATE TABLE category_taxonomy_membership (
    category_id INTEGER NOT NULL REFERENCES categories(id),
    node_id     INTEGER NOT NULL REFERENCES category_taxonomy_nodes(id),
    PRIMARY KEY (category_id, node_id)
);

CREATE TABLE exchange_rates (
    date     DATE NOT NULL,
    currency TEXT NOT NULL,
    rate     DECIMAL(10,4) NOT NULL,
    PRIMARY KEY (date, currency)
);
