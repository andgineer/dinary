ALTER TABLE sheet_import_sources ADD COLUMN income_worksheet_name TEXT;
UPDATE sheet_import_sources SET income_worksheet_name = '' WHERE income_worksheet_name IS NULL;
ALTER TABLE sheet_import_sources ALTER COLUMN income_worksheet_name SET NOT NULL;
ALTER TABLE sheet_import_sources ALTER COLUMN income_worksheet_name SET DEFAULT '';

ALTER TABLE sheet_import_sources ADD COLUMN income_layout_key TEXT;
UPDATE sheet_import_sources SET income_layout_key = '' WHERE income_layout_key IS NULL;
ALTER TABLE sheet_import_sources ALTER COLUMN income_layout_key SET NOT NULL;
ALTER TABLE sheet_import_sources ALTER COLUMN income_layout_key SET DEFAULT '';
