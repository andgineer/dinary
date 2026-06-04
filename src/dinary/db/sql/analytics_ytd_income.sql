SELECT COALESCE(SUM(amount), 0) AS ytd_income
FROM income
WHERE strftime('%Y', income_date) = strftime('%Y', 'now')
