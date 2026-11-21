# Data dictionary — DA-04 Arcadia Lending

Every dataset in the pipeline, from the raw Lending Club file to the Excel extract.
Metric formulas are in [metric_definitions.md](metric_definitions.md).

## 1. Source data

| Dataset | Origin | Used for |
|---|---|---|
| `accepted_2007_to_2018Q4.csv.gz` (1.6 GB, 2.26M loans, 151 columns) | Kaggle: *Lending Club* by wordsforthewise. Not in the repo; download to `data/raw/` | Everything |
| `data/processed/loans_2014_2018.parquet` (2,029,952 loans, 40 columns) | `src/profile_data.py` | Scorecard development (2014–2015) and Arcadia's book (2016–2018) |

Columns kept are split into two groups to prevent leakage:
- **Application** (known when the loan is approved; allowed in the scorecard): `loan_amnt, term, int_rate, installment, grade, sub_grade, emp_length, home_ownership, annual_inc, verification_status, purpose, addr_state, dti, delinq_2yrs, earliest_cr_line, fico_range_low, fico_range_high, inq_last_6mths, open_acc, pub_rec, revol_bal, revol_util, total_acc, mort_acc, pub_rec_bankruptcies`
- **Outcome** (known only later; used for the financials, never as model inputs): `issue_d, loan_status, funded_amnt, out_prncp, total_pymnt, total_rec_prncp, total_rec_int, total_rec_late_fee, recoveries, collection_recovery_fee, last_pymnt_d, last_pymnt_amnt`

## 2. MySQL warehouse `arcadia_finance` (star schema)

Created by `sql/01_create_schema.sql`, filled by the Python loaders. MySQL 8.0+.

### Dimensions

| Table | Grain / key | Rows | Columns |
|---|---|---|---|
| `dim_month` | one row per month · `month_start` | 72 (2014–2019) | `month_end, year_num, quarter_num, month_num, month_name` ('Mar'), `year_month_label` ('2018-03'), `fiscal_year` |
| `dim_account` | chart of accounts · `account_code` | 18 | `account_name`, `account_type` (Asset/Liability/Equity/Revenue/Expense), `statement` (BS/PL), `report_line` (report grouping), `normal_balance` (Debit/Credit), `sort_order` |
| `dim_department` | cost centre · `department_code` | 6 | `department_name` (ALL, RISK, OPS, MKT, TECH, FIN) |
| `dim_grade` | Lending Club grade · `grade` | 7 | `grade_label` ('A - Prime' … 'G - Highest risk'), `sort_order` |
| `dim_risk_band` | scorecard band · `risk_band` | 6 | `sort_order` (1 = R1 safest), `score_from, score_to, train_bad_rate, oot_bad_rate, avg_pd_36m` |

Chart of accounts: 1000 Cash · 1100 Loans receivable (gross) · 1150 Allowance for credit losses ·
2000 Funding facility borrowings · 2100 Income tax payable · 3000 Share capital · 3100 Retained earnings ·
4000 Interest income · 4100 Late fee income · 4200 Origination fee income · 5000 Interest expense ·
5100 Provision for credit losses · 6000 Salaries · 6100 Marketing · 6200 Technology ·
6300 Collections & servicing · 6400 G&A · 7000 Income tax.

### Facts

| Table | Grain / key | Rows | Columns |
|---|---|---|---|
| `fact_loan` | one loan in Arcadia's book · `loan_id` | 68,661 | `issue_month, term_months, grade, sub_grade, purpose, addr_state, loan_amnt, funded_amnt`, `orig_fee_rate` / `orig_fee_amt` (assumption by grade), `int_rate` (annual %), `installment, annual_inc`, `fico_score` (midpoint), `loan_status` (as of Mar 2019), `score_points, pd_12m, risk_band` (from the scorecard) |
| `fact_loan_month` | loan × month while alive · (`loan_id, month_start`) | 1,143,052 | `opening_balance, disbursed_amt, interest_paid, principal_paid, late_fees_paid, charge_off_amt, recovery_amt, closing_balance`, `month_status` (Current / Delinquent / Paid Off / Charged Off) |
| `fact_gl` | one journal line · `gl_line_id` | 4,123 lines / 492 journals | `journal_id` (e.g. `LN-2018-03-INT`), `month_start, account_code`, `department_code` (opex only), `grade` (loan lines only), `debit, credit` (one side only), `source_system` (LOANS, OPEX, FUNDING, PROVISION, EQUITY, TAX), `description` |
| `fact_budget` | month × account × department · composite key | 180 (FY2018) | `budget_amount` (P&L sign: revenue and expense positive) |
| `fact_budget_driver` | month × grade · composite key | 84 (FY2018) | `planned_originations, planned_opening_bal, planned_yield, planned_loss_rate` |
| `fact_kpi_driver` | month × KPI · composite key | 252 | `kpi_code` (HEADCOUNT, COST_PER_HEAD, NEW_LOANS, CAC, ORIGINATIONS, FUNDING_RATE, OPENING_BORROWINGS), `actual_value`, `budget_value` (FY2018 only) |

GL is posted at month × grade (loans) or month × department (opex), not per loan. Loan-level
detail lives in the sub-ledger `fact_loan_month`, and controls prove the two agree.

### Reporting views (`sql/04_reporting_views.sql`)

| View | Grain | Purpose |
|---|---|---|
| `vw_trial_balance` | month × account | movement and running balance for every account |
| `vw_pl_monthly` | month | P&L lines from interest income to net income |
| `vw_balance_sheet` | month-end | balance sheet + `balance_check` (must be 0) |
| `vw_budget_vs_actual` | month × account (FY2018) | actual, budget, favourable variance, variance % |
| `vw_interest_variance_drivers` | month (FY2018) | interest variance split into volume / mix / rate |
| `vw_kpi_variance` | month × KPI | operating drivers actual vs budget |
| `vw_portfolio_monthly` | month × grade | balances, originations, delinquency, yield, net loss rate |
| `vw_portfolio_by_risk_band` | month × risk band | balance, delinquency, average PD and score by band |

## 3. Excel extract (`excel/data/*.csv`)

Written by `src/export_for_excel.py`, loaded by Power Query into the Power Pivot model
(`excel/Arcadia_Board_Report.xlsx`). Dates `YYYY-MM-DD`, decimal point `.`, period Jan 2016 – Dec 2018.

| File | Source |
|---|---|
| `dim_month, dim_account, dim_department, dim_grade, dim_risk_band` | dimension tables; `dim_account` adds `report_line_order` (one sort number per report line) |
| `fact_gl` | `fact_gl` + `net_debit` (debit − credit); missing department shown as `ALL` |
| `fact_budget, fact_budget_driver, fact_kpi_driver` | fact tables |
| `portfolio_monthly, portfolio_by_risk_band, interest_variance_drivers` | the views of the same name |
| `check_pl_monthly, check_balance_sheet` | `vw_pl_monthly`, `vw_balance_sheet`: used only to reconcile Excel to SQL |

Power Pivot adds two calculated columns: `dim_account[pl_sign]` (+1 revenue, −1 expense) and
`fact_gl[pl_amount]` (P&L amount as a positive number; blank for balance-sheet lines).

## 4. Model and report outputs

| File | Content |
|---|---|
| `models/scorecard.json` | bins, WoE, coefficients, points, scaling, calibration shift, band cut-offs: everything needed to score a loan |
| `reports/scorecard/iv_summary.csv` | Information Value of every candidate feature, selected or not |
| `reports/scorecard/scorecard_points.csv` | points table: feature × bin × points |
| `reports/scorecard/validation_metrics.csv` | AUC, Gini, KS on train / validation / out-of-time + Lending Club benchmark |
| `reports/scorecard/risk_bands.csv` | score ranges and bad rates per band |
| `reports/scorecard/stability_psi.csv` | population stability |
| `reports/scorecard/*.png` | ROC curve, bad rate by band |
| `docs/sample_board_report_2018-12.pdf` | sample of the PDF the Power Automate flow emails |
