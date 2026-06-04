SELECT
    COALESCE(SUM(CASE WHEN strftime('%Y-%m', datetime) = strftime('%Y-%m', 'now')
                      THEN amount ELSE 0 END), 0) AS this_month,
    COALESCE(SUM(CASE WHEN strftime('%Y-%m', datetime) = strftime('%Y-%m', 'now', '-1 month')
                      THEN amount ELSE 0 END), 0) AS last_month,
    COALESCE(SUM(CASE WHEN strftime('%Y', datetime) = strftime('%Y', 'now')
                      THEN amount ELSE 0 END), 0) AS ytd_expenses
FROM expenses
