-- =====================================================================
-- Arcadia Lending - Finance & Risk Warehouse
-- 04_reporting_views.sql : the reports, built as views on the GL
--   vw_trial_balance            every account, every month: movement + closing balance
--   vw_pl_monthly               P&L, one row per month (wide)
--   vw_balance_sheet            balance sheet, one row per month-end (wide) + balance check
--   vw_budget_vs_actual         FY2018 actual vs budget by month and account, with variance
--   vw_interest_variance_drivers interest income variance split into volume / mix / rate
--   vw_kpi_variance             operating drivers actual vs budget (headcount, CAC, rate...)
--   vw_portfolio_monthly        loan book health by month and grade (for dashboards)
-- Re-runnable. Requires MySQL 8.0+ (window functions, CTEs).
-- Sign convention for reports: revenue and expenses are both shown as POSITIVE numbers;
-- variance is shown as FAVOURABLE (+) / UNFAVOURABLE (-).
-- =====================================================================
USE arcadia_finance;

-- ---------------------------------------------------------------------
-- Trial balance: debit-positive movement and running balance per account.
-- Cross join months x accounts so months with no postings still show a balance.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW vw_trial_balance AS
WITH movement AS (
    SELECT month_start, account_code, SUM(debit) - SUM(credit) AS net_debit
    FROM fact_gl
    GROUP BY month_start, account_code
)
SELECT
    m.month_start,
    m.fiscal_year,
    a.account_code,
    a.account_name,
    a.account_type,
    a.statement,
    a.report_line,
    COALESCE(mv.net_debit, 0) AS period_net_debit,
    SUM(COALESCE(mv.net_debit, 0)) OVER (
        PARTITION BY a.account_code ORDER BY m.month_start
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS closing_net_debit
FROM dim_month m
CROSS JOIN dim_account a
LEFT JOIN movement mv
       ON mv.month_start = m.month_start AND mv.account_code = a.account_code
WHERE m.month_start BETWEEN (SELECT MIN(month_start) FROM fact_gl)
                        AND (SELECT MAX(month_start) FROM fact_gl);

-- ---------------------------------------------------------------------
-- P&L by month. Revenue = credits - debits; expenses = debits - credits.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW vw_pl_monthly AS
WITH p AS (
    SELECT
        month_start,
        fiscal_year,
        SUM(CASE WHEN account_code = 4000 THEN -period_net_debit ELSE 0 END)            AS interest_income,
        SUM(CASE WHEN account_code IN (4100, 4200) THEN -period_net_debit ELSE 0 END)   AS fee_income,
        SUM(CASE WHEN account_code = 5000 THEN period_net_debit ELSE 0 END)             AS interest_expense,
        SUM(CASE WHEN account_code = 5100 THEN period_net_debit ELSE 0 END)             AS provision_expense,
        SUM(CASE WHEN account_code BETWEEN 6000 AND 6999 THEN period_net_debit ELSE 0 END) AS operating_expenses,
        SUM(CASE WHEN account_code = 7000 THEN period_net_debit ELSE 0 END)             AS income_tax
    FROM vw_trial_balance
    WHERE statement = 'PL'
    GROUP BY month_start, fiscal_year
)
SELECT
    month_start,
    fiscal_year,
    interest_income,
    fee_income,
    interest_income + fee_income                                        AS total_revenue,
    interest_expense,
    interest_income + fee_income - interest_expense                     AS net_revenue,
    provision_expense,
    interest_income + fee_income - interest_expense - provision_expense AS risk_adjusted_revenue,
    operating_expenses,
    interest_income + fee_income - interest_expense - provision_expense
        - operating_expenses                                            AS profit_before_tax,
    income_tax,
    interest_income + fee_income - interest_expense - provision_expense
        - operating_expenses - income_tax                               AS net_income
FROM p;

-- ---------------------------------------------------------------------
-- Balance sheet at each month-end.
-- Retained earnings = all profit since launch (cumulative P&L), so no closing entries are needed.
-- balance_check must be 0.00 every month.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW vw_balance_sheet AS
WITH b AS (
    SELECT
        month_start,
        fiscal_year,
        SUM(CASE WHEN account_code = 1000 THEN closing_net_debit ELSE 0 END)  AS cash,
        SUM(CASE WHEN account_code = 1100 THEN closing_net_debit ELSE 0 END)  AS loans_gross,
        SUM(CASE WHEN account_code = 1150 THEN closing_net_debit ELSE 0 END)  AS allowance_for_credit_losses, -- negative
        SUM(CASE WHEN account_code = 2000 THEN -closing_net_debit ELSE 0 END) AS borrowings,
        SUM(CASE WHEN account_code = 2100 THEN -closing_net_debit ELSE 0 END) AS income_tax_payable,
        SUM(CASE WHEN account_code = 3000 THEN -closing_net_debit ELSE 0 END) AS share_capital,
        SUM(CASE WHEN statement = 'PL'    THEN -closing_net_debit ELSE 0 END) AS retained_earnings
    FROM vw_trial_balance
    GROUP BY month_start, fiscal_year
)
SELECT
    month_start,
    fiscal_year,
    cash,
    loans_gross,
    allowance_for_credit_losses,
    loans_gross + allowance_for_credit_losses                 AS loans_net,
    cash + loans_gross + allowance_for_credit_losses          AS total_assets,
    borrowings,
    income_tax_payable,
    borrowings + income_tax_payable                           AS total_liabilities,
    share_capital,
    retained_earnings,
    share_capital + retained_earnings                         AS total_equity,
    borrowings + income_tax_payable + share_capital + retained_earnings AS total_liabilities_and_equity,
    ROUND((cash + loans_gross + allowance_for_credit_losses)
        - (borrowings + income_tax_payable + share_capital + retained_earnings), 2) AS balance_check
FROM b;

-- ---------------------------------------------------------------------
-- Budget vs actual (FY2018) by month and account.
-- variance > 0 means FAVOURABLE (more revenue or less cost than budget).
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW vw_budget_vs_actual AS
WITH actual AS (
    SELECT month_start, account_code,
           CASE WHEN account_code < 5000 THEN -period_net_debit ELSE period_net_debit END AS actual_amount
    FROM vw_trial_balance
    WHERE statement = 'PL'
),
budget AS (
    SELECT month_start, account_code, SUM(budget_amount) AS budget_amount
    FROM fact_budget
    GROUP BY month_start, account_code
)
SELECT
    b.month_start,
    m.fiscal_year,
    m.quarter_num,
    a.account_code,
    a.account_name,
    a.report_line,
    CASE WHEN a.account_code < 5000 THEN 'Revenue' ELSE 'Expense' END   AS line_type,
    COALESCE(ac.actual_amount, 0)                                      AS actual_amount,
    b.budget_amount,
    CASE WHEN a.account_code < 5000
         THEN COALESCE(ac.actual_amount, 0) - b.budget_amount
         ELSE b.budget_amount - COALESCE(ac.actual_amount, 0) END      AS variance_fav,
    CASE WHEN b.budget_amount = 0 THEN NULL
         ELSE ROUND((CASE WHEN a.account_code < 5000
                          THEN COALESCE(ac.actual_amount, 0) - b.budget_amount
                          ELSE b.budget_amount - COALESCE(ac.actual_amount, 0) END)
                    / ABS(b.budget_amount), 4) END                     AS variance_pct
FROM budget b
JOIN dim_account a ON a.account_code = b.account_code
JOIN dim_month   m ON m.month_start  = b.month_start
LEFT JOIN actual ac ON ac.month_start = b.month_start AND ac.account_code = b.account_code;

-- ---------------------------------------------------------------------
-- WHY did interest income differ from budget?  Split the variance into:
--   volume : the loan book was bigger/smaller than planned        (at planned average yield)
--   mix    : the book held a different blend of grades than planned (each grade at planned yield)
--   rate   : each grade earned a different yield than planned
-- volume + mix + rate = actual - budget, exactly (checked in 05_reconcile_reports.sql).
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW vw_interest_variance_drivers AS
WITH act AS (   -- actual balance and interest by month x grade, from the loan book
    SELECT lm.month_start, l.grade,
           SUM(lm.opening_balance) AS act_bal,
           SUM(lm.interest_paid)   AS act_int
    FROM fact_loan_month lm
    JOIN fact_loan l ON l.loan_id = lm.loan_id
    WHERE lm.month_start IN (SELECT DISTINCT month_start FROM fact_budget_driver)
    GROUP BY lm.month_start, l.grade
),
g AS (          -- one row per month x grade with actual and plan side by side
    SELECT d.month_start, d.grade,
           COALESCE(a.act_bal, 0)              AS act_bal,
           COALESCE(a.act_int, 0)              AS act_int,
           d.planned_opening_bal               AS bud_bal,
           -- "* 1E0" switches to floating-point maths: MySQL's DECIMAL division keeps only
           -- 4 extra decimals (div_precision_increment), which left ~10 cents/month unexplained
           d.planned_yield * 1E0 / 12          AS bud_yield_m,
           d.planned_opening_bal * d.planned_yield * 1E0 / 12 AS bud_int
    FROM fact_budget_driver d
    LEFT JOIN act a ON a.month_start = d.month_start AND a.grade = d.grade
),
t AS (          -- month totals needed for the volume and mix effects
    SELECT month_start,
           SUM(act_bal) AS tot_act_bal,
           SUM(bud_bal) AS tot_bud_bal,
           SUM(bud_int) * 1E0 / SUM(bud_bal) AS bud_avg_yield_m
    FROM g GROUP BY month_start
)
SELECT
    g.month_start,
    YEAR(g.month_start)                                                     AS fiscal_year,
    SUM(g.act_int)                                                          AS actual_interest,
    SUM(g.bud_int)                                                          AS budget_interest,
    SUM(g.act_int) - SUM(g.bud_int)                                         AS total_variance,
    MAX((t.tot_act_bal - t.tot_bud_bal) * t.bud_avg_yield_m)                AS volume_effect,
    SUM((g.act_bal - t.tot_act_bal * g.bud_bal * 1E0 / t.tot_bud_bal) * g.bud_yield_m) AS mix_effect,
    SUM(g.act_int - g.act_bal * g.bud_yield_m)                              AS rate_effect,
    MAX(t.tot_act_bal)                                                      AS actual_opening_balance,
    MAX(t.tot_bud_bal)                                                      AS budget_opening_balance
FROM g JOIN t ON t.month_start = g.month_start
GROUP BY g.month_start;

-- ---------------------------------------------------------------------
-- Operating drivers, actual vs budget (FY2018 only has a budget)
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW vw_kpi_variance AS
SELECT
    k.month_start,
    m.fiscal_year,
    k.kpi_code,
    k.actual_value,
    k.budget_value,
    k.actual_value - k.budget_value                                    AS difference,
    CASE WHEN k.budget_value IS NULL OR k.budget_value = 0 THEN NULL
         ELSE ROUND((k.actual_value - k.budget_value) / k.budget_value, 4) END AS difference_pct
FROM fact_kpi_driver k
JOIN dim_month m ON m.month_start = k.month_start;

-- ---------------------------------------------------------------------
-- Loan book health by month x grade (feeds dashboards)
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW vw_portfolio_monthly AS
SELECT
    lm.month_start,
    YEAR(lm.month_start)                                                AS fiscal_year,
    l.grade,
    COUNT(CASE WHEN lm.closing_balance > 0 THEN 1 END)                  AS active_loans,
    SUM(lm.disbursed_amt)                                               AS originations,
    SUM(lm.opening_balance)                                             AS opening_balance,
    SUM(lm.closing_balance)                                             AS closing_balance,
    SUM(CASE WHEN lm.month_status = 'Delinquent' THEN lm.closing_balance ELSE 0 END) AS delinquent_balance,
    SUM(lm.interest_paid)                                               AS interest_received,
    SUM(lm.charge_off_amt)                                              AS charge_offs,
    SUM(lm.recovery_amt)                                                AS recoveries,
    -- annualised rates, as % of the month's opening balance
    CASE WHEN SUM(lm.opening_balance) > 0
         THEN SUM(lm.interest_paid) * 12 / SUM(lm.opening_balance) END   AS annualised_yield,
    CASE WHEN SUM(lm.opening_balance) > 0
         THEN (SUM(lm.charge_off_amt) - SUM(lm.recovery_amt)) * 12 / SUM(lm.opening_balance) END AS annualised_net_loss_rate,
    CASE WHEN SUM(lm.closing_balance) > 0
         THEN SUM(CASE WHEN lm.month_status = 'Delinquent' THEN lm.closing_balance ELSE 0 END)
              / SUM(lm.closing_balance) END                              AS delinquency_rate
FROM fact_loan_month lm
JOIN fact_loan l ON l.loan_id = lm.loan_id
WHERE lm.month_start <= (SELECT MAX(month_start) FROM fact_gl)
GROUP BY lm.month_start, YEAR(lm.month_start), l.grade;
