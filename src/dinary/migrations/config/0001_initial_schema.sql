CREATE TABLE category_groups (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    monthly_budget_eur DECIMAL(10,2)
);

CREATE TABLE categories (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    group_id    INTEGER NOT NULL REFERENCES category_groups(id),
    UNIQUE(name, group_id)
);

CREATE TABLE family_members (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE
);

CREATE TABLE events (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    date_from   DATE NOT NULL,
    date_to     DATE NOT NULL,
    is_active   BOOLEAN DEFAULT true,
    comment     TEXT
);

CREATE TABLE event_members (
    event_id    INTEGER NOT NULL REFERENCES events(id),
    member_id   INTEGER NOT NULL REFERENCES family_members(id),
    PRIMARY KEY (event_id, member_id)
);

CREATE TABLE tags (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE
);

CREATE TABLE stores (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    store_type  TEXT
);

CREATE TABLE sheet_category_mapping (
    sheet_category  TEXT NOT NULL,
    sheet_group     TEXT NOT NULL DEFAULT '',
    category_id     INTEGER NOT NULL REFERENCES categories(id),
    beneficiary_id  INTEGER REFERENCES family_members(id),
    event_id        INTEGER REFERENCES events(id),
    store_id        INTEGER REFERENCES stores(id),
    tag_ids         INTEGER[],
    PRIMARY KEY (sheet_category, sheet_group)
);
