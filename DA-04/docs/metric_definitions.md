# Metric definitions — DA-04 Arcadia Lending

Every number in the SQL views, the Excel Power Pivot model and the monthly board report is defined
here: what it means, how it is calculated, where it comes from, and how to read its sign.
If a number on a report cannot be traced to a row in this document, it should not be on the report.

---

## 1. Conventions (apply to every metric)

| Topic | Rule |
|---|---|
| Currency | US dollars (USD). Lending Club data is in USD; no conversion. |
| Time grain | Monthly. `month_start` = first day of the month (e.g. 2018-03-01). Fiscal year = calendar year. |
| Reporting period | Jan 2016 – Dec 2018. Loan statuses are "as of" the data snapshot, **Mar 2019**. |
| Flow vs stock | **Flow** metrics (income, expense, originations) are **summed** over the period. **Stock** metrics (balances) are taken **at the end** of the last month in the period, never summed. |
| GL sign | In `fact_gl`, `net_debit = debit − credit`. Assets/expenses are debit-positive; liabilities/equity/revenue are credit-positive. |
| Report sign | On reports, revenue **and** expenses are shown as positive numbers. Losses are shown in brackets. |
| Variance sign | **Favourable (+) / unfavourable (−)**: more revenue or less cost than budget is positive. |
| Year-to-date (YTD) | Jan of the selected month's year up to and including the selected month. |
| Annualised rates | Monthly rate × 12, weighted by opening balance. |
| Source of truth | MySQL views (`sql/04_reporting_views.sql`). The Excel DAX measures recompute the same numbers and are reconciled to SQL to the cent (section 9). |
| Data origin | **Real**: loan amounts, rates, payments, charge-offs, recoveries (Lending Club). **Assumption**: origination fee rates, LGD, funding rate, equity, tax rate. **Synthetic**: operating expenses and the budget (fixed random seed). |

---

## 2. Profit & loss (flow metrics)

Source: `vw_pl_monthly` (SQL) · DAX measures on table `fact_gl` · accounts in `dim_account`.

| Metric | Definition | Formula | Accounts |
|---|---|---|---|
| Interest Income | Interest received from borrowers in the period | Σ credits − debits | 4000 |
| Fee Income | Late fees received + origination fees earned on new loans | Σ credits − debits | 4100, 4200 |
| Total Revenue | All income from lending | Interest Income + Fee Income | 4xxx |
| Interest Expense | Interest paid on the funding facility | Σ debits − credits; = opening borrowings × annual rate ÷ 12 | 5000 |
| Net Revenue | Revenue after the cost of funding | Total Revenue − Interest Expense | |
| Provision Expense | Cost of expected credit losses: the charge needed to bring the allowance to its target (section 5) | target allowance − (opening allowance − charge-offs + recoveries) | 5100 |
| Risk-Adjusted Revenue | Revenue after funding and credit losses | Net Revenue − Provision Expense | |
| Operating Expenses | Salaries, marketing, technology, collections & servicing, G&A | Σ debits − credits | 6000–6400 |
| Profit Before Tax | | Risk-Adjusted Revenue − Operating Expenses | |
| Income Tax | 25% of cumulative profit since launch (losses carried forward), booked monthly | Δ [25% × max(cumulative PBT, 0)] | 7000 |
| **Net Income** | Profit after tax | Profit Before Tax − Income Tax | |

FY2018 check: Total Revenue **$71,291,007**, Net Income **−$14,944**.

## 3. Balance sheet (stock metrics, at month-end)

Source: `vw_balance_sheet` · DAX `Closing Balance` = Σ `net_debit` from launch to the selected month-end.

| Metric | Definition | Account / formula |
|---|---|---|
| Cash Balance | Cash held | 1000 |
| Loans Gross | Principal still owed by borrowers | 1100; must equal Σ `fact_loan_month.closing_balance` |
| Allowance for Credit Losses | Expected credit losses set aside against loans (contra-asset, shown negative) | 1150 |
| Loans Net | Loans Gross + Allowance (allowance is negative) | |
| Total Assets | Cash + Loans Net | |
| Total Borrowings | Funding facility drawn | − balance of 2000 |
| Income Tax Payable | Tax owed, paid each April for the prior year. Negative = refund due | − balance of 2100 |
| Total Liabilities | Borrowings + Tax Payable | |
| Share Capital | Equity raised at launch ($75m, Jan 2016) | − balance of 3000 |
| Retained Earnings | All net income since launch (no closing entries needed) | − Σ all P&L accounts to date |
| Total Equity | Share Capital + Retained Earnings | |
| **Balance Check** | Must be **0.00** every month | Total Assets − (Total Liabilities + Total Equity) |

Dec-2018 check: Total Assets **$513,112,780**, Loans Gross **$537,453,384**, Total Equity **$75,119,813**.

## 4. Budget vs actual and variance drivers

Source: `vw_budget_vs_actual`, `vw_interest_variance_drivers`, `vw_kpi_variance` · budget exists for **FY2018 only**.

| Metric | Definition | Formula |
|---|---|---|
| Budget Amount | Planned P&L amount, same sign convention as actuals | Σ `fact_budget.budget_amount` |
| Variance (Fav) | Favourable variance | Revenue: Actual − Budget. Expense: Budget − Actual |
| Variance % | Variance relative to budget | Variance (Fav) ÷ \|Budget\| |
| Budget Net Income | Planned net income | Σ budget revenue − Σ budget expenses |
| Actual / Budget / Variance YTD | Same as above, Jan to selected month | |

**Interest income variance split** (per month, across grades *g*). Let *A* = actual opening balance,
*B* = budget opening balance, *y* = budget monthly yield, *I* = actual interest received:

| Effect | Meaning | Formula |
|---|---|---|
| Volume | The book was bigger/smaller than planned (at planned average yield) | (ΣA − ΣB) × (ΣB·y ÷ ΣB) |
| Mix | The book held a different blend of grades than planned | Σ (A_g − ΣA × B_g ÷ ΣB) × y_g |
| Rate | Each grade earned a different yield than planned | Σ (I_g − A_g × y_g) |
| Interest Variance | Actual − budget interest income | Volume + Mix + Rate (**exact**, checked by control) |

Each effect also has a YTD version (`Volume Effect YTD`, `Mix Effect YTD`, `Rate Effect YTD`,
`Interest Variance YTD`): the same sum from January to the selected month, used on the board report.

FY2018 check: Volume **+$236,952**, Mix **−$1,269,039**, Rate **+$893,106**, total **−$138,980**.
January's volume and mix are 0 by design: the budget starts from the actual Dec-2017 book.

## 5. Loan portfolio and credit

Source: `vw_portfolio_monthly` (by grade), `vw_portfolio_by_risk_band`, `fact_loan_month`.

| Metric | Type | Definition | Formula |
|---|---|---|---|
| Originations | Flow | Principal lent to new borrowers | Σ `disbursed_amt` |
| Opening / Closing Balance | Stock | Principal owed at start / end of month | |
| Active Loans | Stock | Loans with a balance > 0 at month-end | count |
| Delinquent Balance | Stock | Balance of loans that stopped paying but are not yet charged off | Σ closing balance where status = Delinquent |
| Delinquency Rate | Stock | Share of the book that is delinquent | Delinquent Balance ÷ Closing Balance (month-end) |
| Charge-offs | Flow | Balance written off, 5 months after the last payment (~150 days past due) | Σ `charge_off_amt` |
| Recoveries | Flow | Cash recovered on charged-off loans, net of collection fees | Σ `recovery_amt` |
| Annualised Yield | Rate | Interest earned per dollar lent, per year | Σ interest ÷ Σ opening balance × 12 |
| Net Loss Rate | Rate | Credit losses per dollar lent, per year | (Σ charge-offs − Σ recoveries) ÷ Σ opening balance × 12 |
| Allowance target (ECL) | Stock | 12-month expected credit loss | Paying loan: balance × PD(12m) × LGD. Delinquent loan: balance × LGD |
| Allowance Coverage | Ratio | Share of the book covered by the allowance | − Allowance ÷ Loans Gross |
| LGD | Assumption | Loss given default: share of a defaulted balance that is lost | **88.6%** = 1 − net recoveries ÷ balance charged off (2014 loans) |

FY2018 check: Annualised Yield **12.65%**, Net Loss Rate **6.67%**; Dec-2018 Delinquency Rate **2.86%**.

## 6. Financial ratios

| Metric | Definition | Formula |
|---|---|---|
| Cost-to-Income Ratio | Share of net revenue consumed by operating costs | Operating Expenses ÷ Net Revenue |
| Borrowings to Equity | Leverage: dollars borrowed per dollar of equity | Total Borrowings ÷ Total Equity |

FY2018 check: Cost-to-Income **27.8%**.

## 7. Operating drivers (KPIs)

Source: `fact_kpi_driver` / `vw_kpi_variance`. Board report shows the selected month.

| KPI code | Definition | Unit |
|---|---|---|
| HEADCOUNT | Staff employed (average for the month) | people |
| COST_PER_HEAD | Salaries ÷ headcount | $ per month |
| NEW_LOANS | Number of loans issued | count |
| CAC | Customer acquisition cost: marketing spend ÷ new loans | $ per loan |
| ORIGINATIONS | Principal lent in the month | $ |
| FUNDING_RATE | Annual interest rate on the funding facility | % |
| OPENING_BORROWINGS | Facility balance at the start of the month | $ |

## 8. Credit scorecard metrics

Source: `src/credit_scorecard.py`, `models/scorecard.json`, `reports/scorecard/*.csv`, `dim_risk_band`.

| Metric | Definition |
|---|---|
| Bad (default) | 36-month loan with final status Charged Off or Default |
| WoE (Weight of Evidence) | For each bin of a feature: ln(% of goods in bin ÷ % of bads in bin). Higher = safer |
| IV (Information Value) | Σ (% goods − % bads) × WoE over a feature's bins. < 0.02 not useful, 0.02–0.1 weak, 0.1–0.3 medium |
| Score | Sum of the points of a borrower's bins. 600 points = odds of 50 good : 1 bad; +20 points doubles the odds |
| PD (36m) | Calibrated probability of default over 36 months: 1 ÷ (1 + exp((score − offset) ÷ factor − shift)) |
| PD (12m) | Annual PD used for the allowance: 1 − (1 − PD36)^(1/3) |
| Risk Band | R1 (safest) – R6 (riskiest), score cut-offs at the 10/30/50/70/90th percentiles of training scores |
| AUC | Probability that a random bad loan scores worse than a random good loan (0.5 = random, 1 = perfect) |
| Gini | 2 × AUC − 1 |
| KS | Largest gap between the cumulative score distributions of bads and goods |
| PSI | Population Stability Index between two score-band mixes: Σ (actual% − expected%) × ln(actual% ÷ expected%). < 0.1 stable |

Results: out-of-time (2015) AUC **0.655**, Gini **0.309**, KS **0.222**; PSI train vs Arcadia book **0.025**.

## 9. Data controls (every value must be 0)

| Control | Where |
|---|---|
| Every loan has monthly history; balances roll forward; disbursed = funded; closed loans end at 0 | `sql/03_reconcile_loan_book.sql`, `tests/test_loan_book.py` |
| Monthly cash flows tie to Lending Club lifetime totals to the cent | `tests/test_loan_book.py` |
| Every journal balances (debits = credits) | `sql/05`, `tests/test_ledger.py` |
| Balance sheet balances every month | `sql/05`, `tests/test_ledger.py`, Excel `Balance Check` |
| GL loans and interest = loan sub-ledger | `sql/05`, `tests/test_ledger.py` |
| Cash never negative | `sql/05`, `tests/test_ledger.py` |
| Volume + Mix + Rate = interest variance | `sql/05`, `tests/test_ledger.py` |
| Every loan has a score, PD and valid risk band | `sql/05`, `tests/test_scorecard.py` |
| Excel net income and total assets = SQL | Excel `Net Income Difference`, `Total Assets Difference` |

## 10. Known limitations that affect these metrics
- Monthly cash flows are reconstructed from Lending Club lifetime totals, so the exact month of a payment is approximate (totals are exact).
- Origination fees are recognised as income when the loan is made (accounting standards would spread them over the loan's life).
- Operating expenses, the budget, the funding rate, equity and tax are assumptions or synthetic, generated with fixed seeds and documented in `src/build_ledger.py`.
- The scorecard is trained on 36-month loans and also scores 60-month loans.
