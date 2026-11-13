-- =====================================================================
-- 05_reconcile_reports.sql
-- Controls a finance team would run before trusting the reports.
-- Every "issues" value must be 0.
-- =====================================================================
USE arcadia_finance;

SELECT 'journals that do not balance' AS check_name, COUNT(*) AS issues
FROM (SELECT journal_id FROM fact_gl GROUP BY journal_id
      HAVING ABS(SUM(debit) - SUM(credit)) > 0.01) x
UNION ALL
SELECT 'months where balance sheet does not balance', COUNT(*)
FROM vw_balance_sheet WHERE ABS(balance_check) > 0.01
UNION ALL
SELECT 'months where GL loans <> loan book', COUNT(*)
FROM vw_balance_sheet bs
JOIN (SELECT month_start, SUM(closing_balance) AS book
      FROM fact_loan_month GROUP BY month_start) lb ON lb.month_start = bs.month_start
WHERE ABS(bs.loans_gross - lb.book) > 0.01
UNION ALL
SELECT 'months where GL interest <> loan book interest', COUNT(*)
FROM vw_pl_monthly p
JOIN (SELECT month_start, SUM(interest_paid) AS int_paid
      FROM fact_loan_month GROUP BY month_start) lb ON lb.month_start = p.month_start
WHERE ABS(p.interest_income - lb.int_paid) > 0.01
UNION ALL
SELECT 'months with negative cash', COUNT(*)
FROM vw_balance_sheet WHERE cash < 0
UNION ALL
SELECT 'months where volume+mix+rate <> interest variance', COUNT(*)
FROM vw_interest_variance_drivers
WHERE ABS(volume_effect + mix_effect + rate_effect - total_variance) > 0.01
UNION ALL
SELECT 'budget months missing (expect 12)', 12 - COUNT(DISTINCT month_start)
FROM fact_budget;

-- FY summary: P&L by year
SELECT fiscal_year,
       ROUND(SUM(total_revenue)/1e6, 2)      AS revenue_m,
       ROUND(SUM(interest_expense)/1e6, 2)   AS interest_exp_m,
       ROUND(SUM(provision_expense)/1e6, 2)  AS provision_m,
       ROUND(SUM(operating_expenses)/1e6, 2) AS opex_m,
       ROUND(SUM(net_income)/1e6, 2)         AS net_income_m
FROM vw_pl_monthly GROUP BY fiscal_year ORDER BY fiscal_year;

-- FY2018 budget vs actual by account
SELECT account_code, account_name,
       ROUND(SUM(actual_amount)/1e6, 2) AS actual_m,
       ROUND(SUM(budget_amount)/1e6, 2) AS budget_m,
       ROUND(SUM(variance_fav)/1e6, 2)  AS variance_fav_m
FROM vw_budget_vs_actual GROUP BY account_code, account_name ORDER BY account_code;

-- FY2018 interest variance drivers
SELECT ROUND(SUM(total_variance)/1e6, 3) AS total_m,
       ROUND(SUM(volume_effect)/1e6, 3)  AS volume_m,
       ROUND(SUM(mix_effect)/1e6, 3)     AS mix_m,
       ROUND(SUM(rate_effect)/1e6, 3)    AS rate_m
FROM vw_interest_variance_drivers;
