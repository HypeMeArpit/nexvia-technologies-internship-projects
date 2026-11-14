"""
Step 4 - Build Arcadia Lending's general ledger (GL) and its FY2018 budget.

Input : data/processed/fact_loan.parquet, fact_loan_month.parquet   (from build_loan_book.py)
Output: data/processed/fact_gl.parquet             every journal line, Jan 2016 - Dec 2018
        data/processed/fact_budget.parquet         FY2018 budget by month x account x department
        data/processed/fact_budget_driver.parquet  FY2018 budget assumptions by month x grade
        data/processed/fact_kpi_driver.parquet     operating drivers (headcount, CAC, funding rate...)
                                                   actual vs budget, used to explain variances

WHERE THE NUMBERS COME FROM
  Real (Lending Club)   : loans lent, interest, principal, late fees, charge-offs, recoveries
  Assumption, documented: origination fee by grade, interim PD by grade, LGD, funding rate,
                          equity raised, tax rate
  Synthetic             : operating expenses (headcount, marketing, tech, collections, G&A) and
                          the budget; generated from drivers with a fixed random seed

DOUBLE ENTRY
Every journal posts equal debits and credits, so the balance sheet must balance every month.
tests/test_ledger.py checks this, along with the GL-to-loan-book reconciliation.
"""

from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path("data/processed")
FIRST_MONTH, LAST_MONTH = pd.Timestamp("2016-01-01"), pd.Timestamp("2018-12-01")
BUDGET_YEAR = 2018
RNG = np.random.default_rng(7)  # fixed seed -> identical synthetic numbers on every run

# ---------------------------------------------------------------- credit assumptions
# Fallback 12-month probability of default by grade, used only for a loan with no scorecard
# PD (normally every loan has one: fact_loan.pd_12m from credit_scorecard.py).
# Derived from 2014 36-month loans: lifetime bad rate -> annual rate via 1 - (1 - lifetime) ** (1/3).
LIFETIME_BAD_RATE_2014 = {"A": 0.0538, "B": 0.1072, "C": 0.1719, "D": 0.2217,
                          "E": 0.2703, "F": 0.2949, "G": 0.3631}
INTERIM_PD_12M = {g: 1 - (1 - r) ** (1 / 3) for g, r in LIFETIME_BAD_RATE_2014.items()}
LGD = 0.886  # loss given default: 1 - net recoveries / balance charged off (2014 loans)

# ---------------------------------------------------------------- funding assumptions
EQUITY_RAISED = 75_000_000          # share capital injected in Jan 2016
TAX_RATE = 0.25
MIN_CASH, CASH_BUFFER_PCT = 5_000_000, 0.02   # keep cash >= max($5m, 2% of loans)
FUNDING_RATE = {2016: 0.0375, 2017: 0.0400}    # facility interest rate, annual
FUNDING_RATE_2018_ACTUAL = lambda m: 0.0425 if m <= 3 else 0.0450   # rate rise from April
FUNDING_RATE_2018_BUDGET = 0.0425

# ---------------------------------------------------------------- opex assumptions (synthetic)
DEPT_SHARE = {"RISK": 0.15, "OPS": 0.30, "MKT": 0.10, "TECH": 0.30, "FIN": 0.15}
COST_PER_HEAD_2016 = {"RISK": 9_500, "OPS": 5_500, "MKT": 7_500, "TECH": 10_000, "FIN": 8_000}
ANNUAL_PAY_RISE = 0.04
CAC_PER_LOAN = {2016: 140, 2017: 150, 2018: 165}   # marketing cost per loan funded (actual)
CAC_BUDGET_2018 = 150
SERVICING_PER_ACTIVE_LOAN, COLLECTIONS_PER_DELINQUENT_LOAN = 1.20, 45.0


def headcount_actual(month: pd.Timestamp) -> float:
    """30 staff at launch, 48 by Dec-16, 60 by Dec-17, 84 by Dec-18 (hired ahead of plan in 2018)."""
    points = {pd.Timestamp("2016-01-01"): 30, pd.Timestamp("2016-12-01"): 48,
              pd.Timestamp("2017-12-01"): 60, pd.Timestamp("2018-12-01"): 84}
    return float(np.interp(month.value, [p.value for p in points], list(points.values())))


def headcount_budget(month: pd.Timestamp) -> float:
    """FY2018 plan: grow from 60 to 76 evenly."""
    return 60 + 16 * (month.month / 12)


def month_range(start, end):
    return pd.date_range(start, end, freq="MS")


# =====================================================================================
# 1. LOAN ACTIVITY BY MONTH x GRADE
# =====================================================================================
def loan_activity(loans: pd.DataFrame, months: pd.DataFrame) -> pd.DataFrame:
    m = months[months["month_start"] <= LAST_MONTH].merge(
        loans[["loan_id", "grade", "orig_fee_amt", "pd_12m"]], on="loan_id")

    # Allowance target per loan-month (expected credit loss over the next 12 months):
    #   still paying  -> balance x PD(12m) x LGD
    #   delinquent    -> balance x LGD           (default is already likely)
    pd12 = m["pd_12m"].fillna(m["grade"].map(INTERIM_PD_12M))
    m["allowance_target"] = np.where(m["month_status"] == "Delinquent",
                                     m["closing_balance"] * LGD,
                                     m["closing_balance"] * pd12 * LGD)
    m["orig_fee"] = np.where(m["disbursed_amt"] > 0, m["orig_fee_amt"], 0.0)
    m["is_active"] = (m["closing_balance"] > 0).astype(int)
    m["is_delinquent"] = (m["month_status"] == "Delinquent").astype(int)
    m["is_new"] = (m["disbursed_amt"] > 0).astype(int)

    agg = m.groupby(["month_start", "grade"], as_index=False).agg(
        disbursed=("disbursed_amt", "sum"), orig_fee=("orig_fee", "sum"),
        interest=("interest_paid", "sum"), principal=("principal_paid", "sum"),
        late_fees=("late_fees_paid", "sum"), charge_off=("charge_off_amt", "sum"),
        recovery=("recovery_amt", "sum"), opening_bal=("opening_balance", "sum"),
        closing_bal=("closing_balance", "sum"), allowance_target=("allowance_target", "sum"),
        new_loans=("is_new", "sum"), active_loans=("is_active", "sum"),
        delinquent_loans=("is_delinquent", "sum"))
    num = agg.select_dtypes("number").columns
    agg[num] = agg[num].round(2)
    return agg


# =====================================================================================
# 2. OPERATING EXPENSES (synthetic, driver-based)
# =====================================================================================
def opex_lines(month, headcount, cac, new_loans, active, delinquent, noise=True):
    """Return [(account_code, department_code, amount)] for one month."""
    years = month.year - 2016
    jitter = (lambda: RNG.normal(1, 0.02)) if noise else (lambda: 1.0)
    lines = []
    for dept, share in DEPT_SHARE.items():
        cost = headcount * share * COST_PER_HEAD_2016[dept] * (1 + ANNUAL_PAY_RISE) ** years
        lines.append((6000, dept, cost * jitter()))
    lines.append((6100, "MKT", new_loans * cac * (RNG.normal(1, 0.06) if noise else 1)))
    lines.append((6200, "TECH", 90_000 * 1.12 ** years * jitter()))
    lines.append((6300, "OPS", active * SERVICING_PER_ACTIVE_LOAN + delinquent * COLLECTIONS_PER_DELINQUENT_LOAN))
    g_and_a = 60_000 * 1.10 ** years + (120_000 if month.month == 3 else 0)  # annual audit in March
    lines.append((6400, "FIN", g_and_a * jitter()))
    return [(a, d, round(float(v), 2)) for a, d, v in lines]


# =====================================================================================
# 3. GENERAL LEDGER
# =====================================================================================
class Ledger:
    def __init__(self):
        self.lines = []

    def post(self, journal_id, month, source, description, entries, grade=None, dept=None):
        """entries: list of (account_code, signed_amount); +ve = debit, -ve = credit. Must net to 0."""
        entries = [(a, round(v, 2)) for a, v in entries if round(v, 2) != 0]
        if not entries:
            return
        imbalance = round(sum(v for _, v in entries), 2)
        assert abs(imbalance) < 0.01, f"{journal_id} does not balance: {imbalance}"
        for account, amount in entries:
            self.lines.append({
                "journal_id": journal_id, "month_start": month, "account_code": account,
                "department_code": dept, "grade": grade,
                "debit": amount if amount > 0 else 0.0, "credit": -amount if amount < 0 else 0.0,
                "source_system": source, "description": description})

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.lines)


def build_gl(act: pd.DataFrame):
    gl = Ledger()
    months = month_range(FIRST_MONTH, LAST_MONTH)
    allowance = {g: 0.0 for g in INTERIM_PD_12M}
    cash, borrowings, tax_payable, cum_pbt, tax_booked = 0.0, 0.0, 0.0, 0.0, 0.0
    tax_payable_at_dec = 0.0  # liability at the last year-end, paid the following April
    monthly = []

    for month in months:
        tag = month.strftime("%Y-%m")
        a = act[act["month_start"] == month]
        cash_in = cash_out = 0.0
        pbt = 0.0  # profit before tax for the month

        if month == FIRST_MONTH:
            gl.post(f"EQ-{tag}-RAISE", month, "EQUITY", "Share capital raised at launch",
                    [(1000, EQUITY_RAISED), (3000, -EQUITY_RAISED)])
            cash_in += EQUITY_RAISED

        # ---- loans, posted by grade -------------------------------------------------
        for r in a.itertuples(index=False):
            g = r.grade
            gl.post(f"LN-{tag}-DISB", month, "LOANS", "Loans disbursed, net of origination fee",
                    [(1100, r.disbursed), (1000, -(r.disbursed - r.orig_fee)), (4200, -r.orig_fee)], grade=g)
            gl.post(f"LN-{tag}-INT", month, "LOANS", "Interest received",
                    [(1000, r.interest), (4000, -r.interest)], grade=g)
            gl.post(f"LN-{tag}-PRIN", month, "LOANS", "Principal repaid",
                    [(1000, r.principal), (1100, -r.principal)], grade=g)
            gl.post(f"LN-{tag}-FEE", month, "LOANS", "Late fees received",
                    [(1000, r.late_fees), (4100, -r.late_fees)], grade=g)
            gl.post(f"LN-{tag}-CO", month, "LOANS", "Loans charged off against allowance",
                    [(1150, r.charge_off), (1100, -r.charge_off)], grade=g)
            gl.post(f"LN-{tag}-REC", month, "LOANS", "Recoveries on charged-off loans",
                    [(1000, r.recovery), (1150, -r.recovery)], grade=g)
            # provision = what it takes to move the allowance to its target
            before = allowance[g] + r.charge_off - r.recovery  # allowance is a credit balance (negative)
            provision = -(r.allowance_target) - before        # negative number = more allowance needed
            gl.post(f"PR-{tag}-ECL", month, "PROVISION", "Provision for expected credit losses",
                    [(5100, -provision), (1150, provision)], grade=g)
            allowance[g] = -r.allowance_target

            cash_in += r.interest + r.principal + r.late_fees + r.recovery
            cash_out += r.disbursed - r.orig_fee
            pbt += r.interest + r.late_fees + r.orig_fee + provision

        # ---- operating expenses ------------------------------------------------------
        opex = opex_lines(month, headcount_actual(month), CAC_PER_LOAN[month.year],
                          a["new_loans"].sum(), a["active_loans"].sum(), a["delinquent_loans"].sum())
        for account, dept, amount in opex:
            gl.post(f"OPX-{tag}-{account}", month, "OPEX", "Operating expense paid",
                    [(account, amount), (1000, -amount)], dept=dept)
            cash_out += amount
            pbt -= amount

        # ---- funding: interest on the facility -------------------------------------
        rate = FUNDING_RATE_2018_ACTUAL(month.month) if month.year == 2018 else FUNDING_RATE[month.year]
        interest_exp = borrowings * rate / 12
        gl.post(f"FN-{tag}-INT", month, "FUNDING", "Interest on funding facility",
                [(5000, interest_exp), (1000, -interest_exp)])
        cash_out += interest_exp
        pbt -= interest_exp

        # ---- tax: 25% of cumulative profit (losses carried forward); paid each April --
        cum_pbt += pbt
        tax_should_be = TAX_RATE * max(cum_pbt, 0.0)
        tax_exp = tax_should_be - tax_booked
        tax_booked = tax_should_be
        gl.post(f"TX-{tag}-EXP", month, "TAX", "Income tax expense",
                [(7000, tax_exp), (2100, -tax_exp)])
        tax_payable += tax_exp
        if month.month == 4 and tax_payable_at_dec > 0:
            paid = tax_payable_at_dec  # pay last year's liability
            gl.post(f"TX-{tag}-PAY", month, "TAX", "Income tax paid for prior year",
                    [(2100, paid), (1000, -paid)])
            tax_payable -= paid
            cash_out += paid
        if month.month == 12:
            tax_payable_at_dec = max(tax_payable, 0.0)

        # ---- funding: draw or repay the facility to keep the cash buffer ------------
        cash += cash_in - cash_out
        loans_gross = act.loc[act["month_start"] == month, "closing_bal"].sum()
        target = max(MIN_CASH, CASH_BUFFER_PCT * loans_gross)
        draw = target - cash if cash < target else -min(borrowings, cash - target)
        gl.post(f"FN-{tag}-DRAW", month, "FUNDING",
                "Facility drawn" if draw > 0 else "Facility repaid",
                [(1000, draw), (2000, -draw)])
        cash += draw
        borrowings += draw

        salaries = sum(v for acct, _, v in opex if acct == 6000)
        marketing = sum(v for acct, _, v in opex if acct == 6100)
        new_loans = a["new_loans"].sum()
        monthly.append({"month_start": month, "cash": cash, "borrowings": borrowings,
                        "loans_gross": loans_gross, "pbt": pbt,
                        # actual operating drivers, compared with the budget's drivers
                        "HEADCOUNT": headcount_actual(month), "COST_PER_HEAD": salaries / headcount_actual(month),
                        "NEW_LOANS": new_loans, "CAC": marketing / new_loans if new_loans else 0.0,
                        "ORIGINATIONS": a["disbursed"].sum(), "FUNDING_RATE": rate,
                        "OPENING_BORROWINGS": borrowings - draw})

    gl_df = gl.frame()
    gl_df.insert(0, "gl_line_id", np.arange(1, len(gl_df) + 1))
    return gl_df, pd.DataFrame(monthly)


# =====================================================================================
# 4. FY2018 BUDGET (made in Dec 2017 from 2017 actuals + planned growth)
# =====================================================================================
def build_budget(act: pd.DataFrame, gl_monthly: pd.DataFrame):
    a17 = act[act["month_start"].dt.year == 2017]
    by_grade = a17.groupby("grade").agg(
        orig=("disbursed", "sum"), interest=("interest", "sum"), principal=("principal", "sum"),
        charge_off=("charge_off", "sum"), late=("late_fees", "sum"), opening=("opening_bal", "sum"),
        new_loans=("new_loans", "sum"))
    avg_bal = by_grade["opening"] / 12
    plan = pd.DataFrame({
        "monthly_orig": by_grade["orig"] / 12 * 1.10,        # plan: +10% volume, same grade mix as 2017
        "yield": by_grade["interest"] / avg_bal,              # realised 2017 yield by grade
        "paydown": by_grade["principal"] / by_grade["opening"],
        "loss_rate": by_grade["charge_off"] / avg_bal * 1.15, # +15% for the book seasoning
        "late_fee_rate": by_grade["late"] / avg_bal,
        "avg_loan": by_grade["orig"] / by_grade["new_loans"],
    })
    fee_rate = (a17.groupby("grade")["orig_fee"].sum() / by_grade["orig"]).rename("fee_rate")
    plan = plan.join(fee_rate)

    dec17 = act[act["month_start"] == "2017-12-01"].set_index("grade")
    bal = dec17["closing_bal"].copy()
    allowance = dec17["allowance_target"].copy()
    borrow_ratio = gl_monthly.set_index("month_start").loc["2017-12-01"]
    borrow_ratio = borrow_ratio["borrowings"] / borrow_ratio["loans_gross"]
    active_per_bal = (dec17["closing_bal"].sum() and
                      act[act["month_start"] == "2017-12-01"]["active_loans"].sum() / dec17["closing_bal"].sum())
    delinq_share = (act[act["month_start"].dt.year == 2017]["delinquent_loans"].sum()
                    / act[act["month_start"].dt.year == 2017]["active_loans"].sum())
    el_rate = allowance / bal  # Dec-17 allowance coverage by grade, kept constant in the plan

    drivers, budget, kpis = [], [], []
    for month in month_range(f"{BUDGET_YEAR}-01-01", f"{BUDGET_YEAR}-12-01"):
        interest = bal * plan["yield"] / 12
        late = bal * plan["late_fee_rate"] / 12
        orig = plan["monthly_orig"]
        charge_off = bal * plan["loss_rate"] / 12
        principal = bal * plan["paydown"]
        new_bal = bal + orig - principal - charge_off
        provision = charge_off + (new_bal * el_rate - bal * el_rate)  # recoveries ignored in plan
        for g in plan.index:
            drivers.append({"month_start": month, "grade": g,
                            "planned_originations": orig[g], "planned_opening_bal": bal[g],
                            "planned_yield": plan.at[g, "yield"], "planned_loss_rate": plan.at[g, "loss_rate"]})

        funding = bal.sum() * borrow_ratio * FUNDING_RATE_2018_BUDGET / 12
        new_loans = (orig / plan["avg_loan"]).sum()
        active = new_bal.sum() * active_per_bal
        opex = opex_lines(month, headcount_budget(month), CAC_BUDGET_2018,
                          new_loans, active, active * delinq_share, noise=False)
        rows = [(4000, "ALL", interest.sum()), (4100, "ALL", late.sum()),
                (4200, "ALL", (orig * plan["fee_rate"]).sum()),
                (5000, "ALL", funding), (5100, "ALL", provision.sum())] + opex
        revenue = sum(v for acct, _, v in rows if acct < 5000)
        pbt = revenue - sum(v for acct, _, v in rows if acct >= 5000)
        rows.append((7000, "ALL", TAX_RATE * max(pbt, 0)))
        budget += [{"month_start": month, "account_code": acct, "department_code": d,
                    "budget_amount": round(v, 2)} for acct, d, v in rows]
        salaries = sum(v for acct, _, v in opex if acct == 6000)
        kpis.append({"month_start": month, "HEADCOUNT": headcount_budget(month),
                     "COST_PER_HEAD": salaries / headcount_budget(month), "NEW_LOANS": new_loans,
                     "CAC": CAC_BUDGET_2018, "ORIGINATIONS": orig.sum(),
                     "FUNDING_RATE": FUNDING_RATE_2018_BUDGET,
                     "OPENING_BORROWINGS": bal.sum() * borrow_ratio})
        bal = new_bal

    drv = pd.DataFrame(drivers)
    drv[["planned_originations", "planned_opening_bal"]] = drv[["planned_originations", "planned_opening_bal"]].round(2)
    drv[["planned_yield", "planned_loss_rate"]] = drv[["planned_yield", "planned_loss_rate"]].round(4)
    return pd.DataFrame(budget), drv, pd.DataFrame(kpis)


KPI_CODES = ["HEADCOUNT", "COST_PER_HEAD", "NEW_LOANS", "CAC", "ORIGINATIONS",
             "FUNDING_RATE", "OPENING_BORROWINGS"]


def build_kpi_drivers(actual: pd.DataFrame, budget: pd.DataFrame) -> pd.DataFrame:
    """Long table: month x KPI with actual and budget values (budget only exists for FY2018)."""
    act = actual.melt(id_vars="month_start", value_vars=KPI_CODES, var_name="kpi_code", value_name="actual_value")
    bud = budget.melt(id_vars="month_start", value_vars=KPI_CODES, var_name="kpi_code", value_name="budget_value")
    out = act.merge(bud, on=["month_start", "kpi_code"], how="left")
    out[["actual_value", "budget_value"]] = out[["actual_value", "budget_value"]].round(6)
    return out


def main():
    loans = pd.read_parquet(DATA / "fact_loan.parquet")
    if "pd_12m" not in loans:
        loans["pd_12m"] = np.nan  # scorecard not run yet -> grade fallback PDs
    months = pd.read_parquet(DATA / "fact_loan_month.parquet")

    act = loan_activity(loans, months)
    gl, monthly = build_gl(act)
    budget, drivers, budget_kpis = build_budget(act, monthly)
    kpi = build_kpi_drivers(monthly, budget_kpis)

    gl.to_parquet(DATA / "fact_gl.parquet", index=False)
    budget.to_parquet(DATA / "fact_budget.parquet", index=False)
    drivers.to_parquet(DATA / "fact_budget_driver.parquet", index=False)
    kpi.to_parquet(DATA / "fact_kpi_driver.parquet", index=False)

    gl["year"] = gl["month_start"].dt.year
    signed = gl.assign(net=gl["credit"] - gl["debit"])
    net_income = signed[signed["account_code"] >= 4000].groupby("year")["net"].sum()
    print(f"fact_gl            : {len(gl):,} lines, {gl['journal_id'].nunique():,} journals")
    print(f"fact_budget        : {len(budget):,} rows   fact_budget_driver: {len(drivers):,} rows")
    print("Net income by year ($m):", (net_income / 1e6).round(2).to_dict())
    print("Budget FY2018 net income ($m):",
          round((budget.assign(s=np.where(budget.account_code < 5000, 1, -1) * budget.budget_amount)["s"].sum()) / 1e6, 2))
    print(monthly.iloc[[0, 11, 23, 35]].assign(
        cash=lambda d: (d.cash / 1e6).round(1), borrowings=lambda d: (d.borrowings / 1e6).round(1),
        loans_gross=lambda d: (d.loans_gross / 1e6).round(1), pbt=lambda d: (d.pbt / 1e6).round(2)).to_string(index=False))


if __name__ == "__main__":
    main()
