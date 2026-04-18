CREATE TABLE income (
    year   INTEGER NOT NULL,
    month  INTEGER NOT NULL,
    amount DECIMAL(12,2) NOT NULL,
    origin TEXT NOT NULL DEFAULT 'sheet_import',
    PRIMARY KEY (year, month)
);
