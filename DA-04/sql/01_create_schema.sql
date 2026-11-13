-- =====================================================================
-- Arcadia Lending - Finance & Risk Warehouse
-- 01_create_schema.sql : creates the database and all tables
-- Target: MySQL 8.0+
-- Re-runnable: drops and recreates everything.
-- =====================================================================

DROP DATABASE IF EXISTS arcadia_finance;
CREATE DATABASE arcadia_finance CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE arcadia_finance;

-- ---------------------------------------------------------------------
-- DIMENSIONS (descriptive "lookup" tables)
-- ---------------------------------------------------------------------

-- One row per calendar month. Every fact table joins to this on month_start.
CREATE TABLE dim_month (
    month_start     DATE        NOT NULL PRIMARY KEY,   -- first day of month, e.g. 2018-03-01
    month_end       DATE        NOT NULL,
    year_num        SMALLINT    NOT NULL,
    quarter_num     TINYINT     NOT NULL,
    month_num       TINYINT     NOT NULL,
    month_name      CHAR(3)     NOT NULL,               -- 'Jan'
    year_month_label CHAR(7)    NOT NULL,               -- '2018-03'
    fiscal_year     SMALLINT    NOT NULL                -- Arcadia's FY = calendar year
);

-- Chart of accounts: the list of every account the GL can post to.
CREATE TABLE dim_account (
    account_code    SMALLINT     NOT NULL PRIMARY KEY,  -- 1000-7999, numbered by type
    account_name    VARCHAR(60)  NOT NULL,
    account_type    ENUM('Asset','Liability','Equity','Revenue','Expense') NOT NULL,
    statement       ENUM('BS','PL') NOT NULL,           -- which statement it appears on
    report_line     VARCHAR(40)  NOT NULL,              -- grouping used on reports
    normal_balance  ENUM('Debit','Credit') NOT NULL,    -- side that increases the account
    sort_order      SMALLINT     NOT NULL
);

-- Departments (cost centres) so opex can be analysed by team.
CREATE TABLE dim_department (
    department_code VARCHAR(10) NOT NULL PRIMARY KEY,
    department_name VARCHAR(40) NOT NULL
);

-- Lending Club credit grades A-G.
CREATE TABLE dim_grade (
    grade           CHAR(1)     NOT NULL PRIMARY KEY,
    grade_label     VARCHAR(20) NOT NULL,
    sort_order      TINYINT     NOT NULL
);

-- ---------------------------------------------------------------------
-- FACTS (the numbers)
-- ---------------------------------------------------------------------

-- One row per loan in Arcadia's book. Scorecard columns are filled on Day 3.
CREATE TABLE fact_loan (
    loan_id          BIGINT        NOT NULL PRIMARY KEY,
    issue_month      DATE          NOT NULL,
    term_months      TINYINT       NOT NULL,
    grade            CHAR(1)       NOT NULL,
    sub_grade        CHAR(2)       NOT NULL,
    purpose          VARCHAR(30),
    addr_state       CHAR(2),
    loan_amnt        DECIMAL(12,2) NOT NULL,
    funded_amnt      DECIMAL(12,2) NOT NULL,            -- principal actually lent
    orig_fee_rate    DECIMAL(5,4)  NOT NULL,            -- synthetic fee by grade, e.g. 0.0400
    orig_fee_amt     DECIMAL(12,2) NOT NULL,            -- deducted from cash paid to borrower
    int_rate         DECIMAL(5,2)  NOT NULL,            -- annual %, e.g. 13.49
    installment      DECIMAL(10,2) NOT NULL,
    annual_inc       DECIMAL(14,2),
    fico_score       SMALLINT,                          -- midpoint of FICO range
    loan_status      VARCHAR(60)   NOT NULL,            -- status as of data extract
    -- filled by the credit scorecard (Day 3)
    score_points     SMALLINT      NULL,
    pd_12m           DECIMAL(7,6)  NULL,                -- probability of default
    risk_band        VARCHAR(20)   NULL,
    CONSTRAINT fk_loan_month FOREIGN KEY (issue_month) REFERENCES dim_month(month_start),
    CONSTRAINT fk_loan_grade FOREIGN KEY (grade)       REFERENCES dim_grade(grade),
    INDEX ix_loan_issue (issue_month),
    INDEX ix_loan_grade (grade)
);

-- One row per loan per month while the loan is alive: the simulated cash flows.
CREATE TABLE fact_loan_month (
    loan_id           BIGINT        NOT NULL,
    month_start       DATE          NOT NULL,
    opening_balance   DECIMAL(12,2) NOT NULL,
    disbursed_amt     DECIMAL(12,2) NOT NULL DEFAULT 0,  -- principal lent this month (issue month only)
    interest_paid     DECIMAL(12,2) NOT NULL DEFAULT 0,
    principal_paid    DECIMAL(12,2) NOT NULL DEFAULT 0,
    late_fees_paid    DECIMAL(12,2) NOT NULL DEFAULT 0,
    charge_off_amt    DECIMAL(12,2) NOT NULL DEFAULT 0,
    recovery_amt      DECIMAL(12,2) NOT NULL DEFAULT 0,
    closing_balance   DECIMAL(12,2) NOT NULL,
    -- Current = paying; Delinquent = stopped paying, not yet written off;
    -- Paid Off = final month of a fully repaid loan; Charged Off = written off (incl. later recovery rows)
    month_status      ENUM('Current','Delinquent','Paid Off','Charged Off') NOT NULL,
    PRIMARY KEY (loan_id, month_start),
    CONSTRAINT fk_lm_loan  FOREIGN KEY (loan_id)     REFERENCES fact_loan(loan_id),
    CONSTRAINT fk_lm_month FOREIGN KEY (month_start) REFERENCES dim_month(month_start),
    INDEX ix_lm_month (month_start)
);

-- General ledger: every journal line. Each journal_id must have debits = credits.
CREATE TABLE fact_gl (
    gl_line_id       BIGINT        NOT NULL AUTO_INCREMENT PRIMARY KEY,
    journal_id       VARCHAR(30)   NOT NULL,            -- e.g. 'LOAN-2018-03-INT'
    month_start      DATE          NOT NULL,
    account_code     SMALLINT      NOT NULL,
    department_code  VARCHAR(10)   NULL,                -- only for opex lines
    grade            CHAR(1)       NULL,                -- only for loan-driven lines
    debit            DECIMAL(16,2) NOT NULL DEFAULT 0,
    credit           DECIMAL(16,2) NOT NULL DEFAULT 0,
    source_system    ENUM('LOANS','OPEX','FUNDING','PROVISION','EQUITY','TAX') NOT NULL,
    description      VARCHAR(120),
    CONSTRAINT chk_gl_one_side CHECK (debit >= 0 AND credit >= 0 AND NOT (debit > 0 AND credit > 0)),
    CONSTRAINT fk_gl_month FOREIGN KEY (month_start)     REFERENCES dim_month(month_start),
    CONSTRAINT fk_gl_acct  FOREIGN KEY (account_code)    REFERENCES dim_account(account_code),
    CONSTRAINT fk_gl_dept  FOREIGN KEY (department_code) REFERENCES dim_department(department_code),
    CONSTRAINT fk_gl_grade FOREIGN KEY (grade)           REFERENCES dim_grade(grade),
    INDEX ix_gl_month_acct (month_start, account_code),
    INDEX ix_gl_journal (journal_id)
);

-- Budget by month and account (same grain as the GL, so they compare directly).
-- Amounts are signed the way they appear on the P&L: revenue positive, expense positive.
CREATE TABLE fact_budget (
    month_start      DATE          NOT NULL,
    account_code     SMALLINT      NOT NULL,
    department_code  VARCHAR(10)   NOT NULL DEFAULT 'ALL',
    budget_amount    DECIMAL(16,2) NOT NULL,
    PRIMARY KEY (month_start, account_code, department_code),
    CONSTRAINT fk_bud_month FOREIGN KEY (month_start)  REFERENCES dim_month(month_start),
    CONSTRAINT fk_bud_acct  FOREIGN KEY (account_code) REFERENCES dim_account(account_code)
);

-- The assumptions behind the budget. Variance analysis compares these to actual drivers.
CREATE TABLE fact_budget_driver (
    month_start          DATE          NOT NULL,
    grade                CHAR(1)       NOT NULL,
    planned_originations DECIMAL(16,2) NOT NULL,       -- $ of new loans
    planned_opening_bal  DECIMAL(16,2) NOT NULL,       -- loan balance at start of month
    planned_yield        DECIMAL(7,4)  NOT NULL,       -- annual interest received / balance, e.g. 0.1350
    planned_loss_rate    DECIMAL(7,4)  NOT NULL,       -- annual charge-offs / balance
    PRIMARY KEY (month_start, grade),
    CONSTRAINT fk_drv_month FOREIGN KEY (month_start) REFERENCES dim_month(month_start),
    CONSTRAINT fk_drv_grade FOREIGN KEY (grade)       REFERENCES dim_grade(grade)
);

-- Operating drivers, actual vs budget (budget only for FY2018). Explains WHY variances happened.
-- kpi_code: HEADCOUNT, COST_PER_HEAD, NEW_LOANS, CAC, ORIGINATIONS, FUNDING_RATE, OPENING_BORROWINGS
CREATE TABLE fact_kpi_driver (
    month_start      DATE          NOT NULL,
    kpi_code         VARCHAR(30)   NOT NULL,
    actual_value     DECIMAL(20,6) NOT NULL,
    budget_value     DECIMAL(20,6) NULL,
    PRIMARY KEY (month_start, kpi_code),
    CONSTRAINT fk_kpi_month FOREIGN KEY (month_start) REFERENCES dim_month(month_start)
);
