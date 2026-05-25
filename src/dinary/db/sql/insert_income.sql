INSERT INTO income (year, month, income_date, amount, amount_original, currency_original, comment)
VALUES (?, ?, ?, ?, ?, ?, ?)
RETURNING id, year, month, income_date, amount, amount_original, currency_original, comment
