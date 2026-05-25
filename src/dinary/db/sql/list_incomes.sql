SELECT id, year, month, income_date, amount, amount_original, currency_original, comment
  FROM income
 ORDER BY income_date DESC, id DESC
 LIMIT ? OFFSET ?
