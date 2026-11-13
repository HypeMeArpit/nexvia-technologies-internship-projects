-- =====================================================================
-- 03_reconcile_loan_book.sql
-- Checks run after load_loan_book.py. Every "issues" column must be 0.
-- =====================================================================
USE arcadia_finance;

-- 1. Every loan has monthly history, and every history row has a loan
SELECT 'loans without history' AS check_name, COUNT(*) AS issues
FROM fact_loan l LEFT JOIN fact_loan_month m ON m.loan_id = l.loan_id
WHERE m.loan_id IS NULL
UNION ALL
-- 2. Balance roll-forward holds on every row: opening + disbursed - principal - charge-off = closing
SELECT 'roll-forward breaks', COUNT(*)
FROM fact_loan_month
WHERE ABS(opening_balance + disbursed_amt - principal_paid - charge_off_amt - closing_balance) > 0.01
UNION ALL
-- 3. Disbursed per loan equals funded amount
SELECT 'disbursed <> funded', COUNT(*)
FROM (SELECT l.loan_id, l.funded_amnt, SUM(m.disbursed_amt) AS disbursed
      FROM fact_loan l JOIN fact_loan_month m ON m.loan_id = l.loan_id
      GROUP BY l.loan_id, l.funded_amnt) x
WHERE ABS(funded_amnt - disbursed) > 0.01
UNION ALL
-- 4. Fully paid and charged-off loans end at zero balance
SELECT 'closed loans with balance', COUNT(*)
FROM fact_loan l
JOIN fact_loan_month m ON m.loan_id = l.loan_id
WHERE l.loan_status IN ('Fully Paid', 'Charged Off')
  AND m.month_start = (SELECT MAX(month_start) FROM fact_loan_month m2 WHERE m2.loan_id = l.loan_id)
  AND ABS(m.closing_balance) > 0.01;

-- Portfolio summary by year (for eyeballing)
SELECT YEAR(month_start)               AS yr,
       ROUND(SUM(disbursed_amt)/1e6, 2) AS disbursed_m,
       ROUND(SUM(interest_paid)/1e6, 2) AS interest_m,
       ROUND(SUM(principal_paid)/1e6, 2) AS principal_m,
       ROUND(SUM(charge_off_amt)/1e6, 2) AS charge_off_m,
       ROUND(SUM(recovery_amt)/1e6, 2)  AS recovery_m
FROM fact_loan_month
GROUP BY YEAR(month_start)
ORDER BY yr;
