ALTER TABLE sheet_import_sources
    ADD COLUMN income_worksheet_name TEXT NOT NULL DEFAULT '';
ALTER TABLE sheet_import_sources
    ADD COLUMN income_layout_key TEXT NOT NULL DEFAULT '';
