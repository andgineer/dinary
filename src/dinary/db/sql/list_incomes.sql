SELECT year, month, amount
  FROM income
 ORDER BY year DESC, month DESC
 LIMIT ? OFFSET ?
