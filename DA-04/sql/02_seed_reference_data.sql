-- =====================================================================
-- Arcadia Lending - Finance & Risk Warehouse
-- 02_seed_reference_data.sql : fills the dimension tables
-- Run after 01_create_schema.sql
-- =====================================================================

USE arcadia_finance;

-- ---------------------------------------------------------------------
-- Months: Jan 2014 - Dec 2019 (covers scorecard years, reporting years, and run-off)
-- ---------------------------------------------------------------------
INSERT INTO dim_month
    (month_start, month_end, year_num, quarter_num, month_num, month_name, year_month_label, fiscal_year)
WITH RECURSIVE months AS (
    SELECT DATE('2014-01-01') AS m
    UNION ALL
    SELECT m + INTERVAL 1 MONTH FROM months WHERE m < '2019-12-01'
)
SELECT
    m,
    LAST_DAY(m),
    YEAR(m),
    QUARTER(m),
    MONTH(m),
    DATE_FORMAT(m, '%b'),
    DATE_FORMAT(m, '%Y-%m'),
    YEAR(m)
FROM months;

-- ---------------------------------------------------------------------
-- Chart of accounts
--   1xxx Assets | 2xxx Liabilities | 3xxx Equity | 4xxx Revenue
--   5xxx Funding & credit costs    | 6xxx Operating expenses | 7xxx Tax
-- ---------------------------------------------------------------------
INSERT INTO dim_account
    (account_code, account_name, account_type, statement, report_line, normal_balance, sort_order)
VALUES
    (1000, 'Cash and cash equivalents',      'Asset',     'BS', 'Cash',                       'Debit',  10),
    (1100, 'Loans receivable - gross',       'Asset',     'BS', 'Loans receivable, net',      'Debit',  20),
    (1150, 'Allowance for credit losses',    'Asset',     'BS', 'Loans receivable, net',      'Credit', 30),  -- contra-asset
    (2000, 'Funding facility borrowings',    'Liability', 'BS', 'Borrowings',                 'Credit', 40),
    (2100, 'Income tax payable',             'Liability', 'BS', 'Other liabilities',          'Credit', 50),
    (3000, 'Share capital',                  'Equity',    'BS', 'Share capital',              'Credit', 60),
    (3100, 'Retained earnings',              'Equity',    'BS', 'Retained earnings',          'Credit', 70),
    (4000, 'Interest income on loans',       'Revenue',   'PL', 'Interest income',            'Credit', 100),
    (4100, 'Late fee income',                'Revenue',   'PL', 'Fee income',                 'Credit', 110),
    (4200, 'Origination fee income',         'Revenue',   'PL', 'Fee income',                 'Credit', 120),
    (5000, 'Interest expense on borrowings', 'Expense',   'PL', 'Interest expense',           'Debit',  200),
    (5100, 'Provision for credit losses',    'Expense',   'PL', 'Provision for credit losses','Debit',  210),
    (6000, 'Salaries and benefits',          'Expense',   'PL', 'Operating expenses',         'Debit',  300),
    (6100, 'Marketing and acquisition',      'Expense',   'PL', 'Operating expenses',         'Debit',  310),
    (6200, 'Technology and data',            'Expense',   'PL', 'Operating expenses',         'Debit',  320),
    (6300, 'Collections and servicing',      'Expense',   'PL', 'Operating expenses',         'Debit',  330),
    (6400, 'General and administrative',     'Expense',   'PL', 'Operating expenses',         'Debit',  340),
    (7000, 'Income tax expense',             'Expense',   'PL', 'Income tax',                 'Debit',  400);

-- ---------------------------------------------------------------------
-- Departments (cost centres)
-- ---------------------------------------------------------------------
INSERT INTO dim_department (department_code, department_name) VALUES
    ('ALL',  'Company-wide'),
    ('RISK', 'Credit Risk'),
    ('OPS',  'Operations & Collections'),
    ('MKT',  'Marketing'),
    ('TECH', 'Technology'),
    ('FIN',  'Finance & Admin');

-- ---------------------------------------------------------------------
-- Credit grades
-- ---------------------------------------------------------------------
INSERT INTO dim_grade (grade, grade_label, sort_order) VALUES
    ('A', 'A - Prime',          1),
    ('B', 'B - Near prime',     2),
    ('C', 'C - Standard',       3),
    ('D', 'D - Elevated risk',  4),
    ('E', 'E - High risk',      5),
    ('F', 'F - Very high risk', 6),
    ('G', 'G - Highest risk',   7);

-- Quick check: expect 72 months, 18 accounts, 6 departments, 7 grades
SELECT 'dim_month' AS tbl, COUNT(*) AS row_count FROM dim_month
UNION ALL SELECT 'dim_account',    COUNT(*) FROM dim_account
UNION ALL SELECT 'dim_department', COUNT(*) FROM dim_department
UNION ALL SELECT 'dim_grade',      COUNT(*) FROM dim_grade;
