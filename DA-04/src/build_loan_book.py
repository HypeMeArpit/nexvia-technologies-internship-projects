"""
Step 2 - Build Arcadia Lending's loan book and its monthly cash flows.

Input : data/processed/loans_2014_2018.parquet   (from profile_data.py)
Output: data/processed/fact_loan.parquet        one row per loan
        data/processed/fact_loan_month.parquet  one row per loan per month

WHY THIS IS NEEDED
Lending Club gives us only LIFETIME totals per loan (total interest received,
total principal received, recoveries, last payment date). A P&L needs MONTHLY
numbers. So for each loan we rebuild a month-by-month history that:
  * follows a normal amortisation schedule (bigger interest early, more principal later),
  * stops paying in the month of the last recorded payment,
  * and adds up EXACTLY to Lending Club's lifetime totals (tested in tests/).

RULES (documented in docs/metric_definitions.md)
  * Snapshot date      : Mar 2019 (latest payment date in the data). Statuses are "as of" then.
  * Payments           : start the month after issue; last one in last_pymnt_d.
  * Early payoff       : Fully Paid loans follow the schedule, then repay the rest in the last month.
  * Missed payments    : other loans have scheduled principal scaled to what was actually received.
  * Interest           : opening balance x monthly rate, scaled to the actual total interest received.
  * Late fees          : booked in the last payment month.
  * Charge-off         : 5 months after the last payment (Lending Club writes off at ~150 days past due).
  * Recoveries         : net of collection fees, received 3 months after charge-off.
  * Origination fee    : SYNTHETIC rate by grade, deducted from the cash paid to the borrower.

Run from the project root:
    python src/build_loan_book.py
"""

from pathlib import Path
import time

import numpy as np
import pandas as pd

IN_FILE = Path("data/processed/loans_2014_2018.parquet")
OUT_LOAN = Path("data/processed/fact_loan.parquet")
OUT_MONTH = Path("data/processed/fact_loan_month.parquet")

BOOK_YEARS = [2016, 2017, 2018]   # Arcadia launched Jan 2016
SAMPLE_FRAC = 0.05                # Arcadia is a smaller lender: 5% of Lending Club's volume
RANDOM_SEED = 42                  # fixed seed -> same sample every run (reproducible)
SNAPSHOT = pd.Timestamp("2019-03-01")
CHARGE_OFF_LAG = 5                # months after last payment
RECOVERY_LAG = 3                  # months after charge-off

# Synthetic origination fee by grade (Lending Club's real range was roughly 1-6%).
ORIG_FEE_BY_GRADE = {"A": 0.01, "B": 0.02, "C": 0.04, "D": 0.05, "E": 0.06, "F": 0.06, "G": 0.06}

BAD_STATUSES = {"Late (31-120 days)", "Late (16-30 days)", "Default"}


def to_mi(ts: pd.Timestamp) -> int:
    """Month index: number of months since year 0. Makes month arithmetic simple integers."""
    return ts.year * 12 + ts.month - 1


def from_mi(mi: np.ndarray) -> pd.DatetimeIndex:
    years, months = np.divmod(mi, 12)
    return pd.to_datetime({"year": years, "month": months + 1, "day": 1})


def round_cumulative(x: np.ndarray) -> np.ndarray:
    """Round a series to cents so its TOTAL is exactly preserved (no penny drift)."""
    return np.diff(np.round(np.cumsum(x), 2), prepend=0.0)


def scheduled_principal(P: float, monthly_rate: float, term: int, length: int) -> np.ndarray:
    """Principal due each month on a standard fixed-payment (EMI) loan, padded with zeros past term."""
    if monthly_rate == 0:
        sched = np.full(term, P / term)
    else:
        k = np.arange(term)
        payment = P * monthly_rate / (1 - (1 + monthly_rate) ** -term)
        # principal portion in month k+1 of an amortising loan
        sched = (payment - P * monthly_rate) * (1 + monthly_rate) ** k
    out = np.zeros(max(length, term))
    out[:term] = sched
    return out[:length]


def simulate_loan(row) -> dict:
    """Return the monthly history of one loan as a dict of equal-length arrays."""
    P = float(row.funded_amnt)
    r = float(row.int_rate) / 1200.0
    n = int(row.term_months)
    TP = float(row.total_rec_prncp)
    TI = float(row.total_rec_int)
    late = float(row.total_rec_late_fee)
    net_recovery = max(float(row.recoveries) - float(row.collection_recovery_fee), 0.0)
    status = row.loan_status

    i0 = to_mi(row.issue_d)
    snap = to_mi(SNAPSHOT)
    last = to_mi(row.last_pymnt_d) if pd.notna(row.last_pymnt_d) else i0
    m = max(last - i0, 0)                       # number of months with a payment
    if m == 0 and (TP > 0 or TI > 0):           # paid something but date missing/odd
        m, last = 1, i0 + 1

    # ---- payment months: principal, balances, interest -------------------------
    if m > 0:
        sched = scheduled_principal(P, r, n, m)
        if status == "Fully Paid":
            # follow the schedule, never exceeding what was actually repaid, then pay off the rest
            prin = np.diff(np.minimum(np.cumsum(sched), TP), prepend=0.0)
            prin[-1] = TP - prin[:-1].sum()
        else:
            total = sched.sum()
            prin = sched * (TP / total) if total > 0 else np.full(m, TP / m)
        prin = round_cumulative(prin)
        opening = np.round(P - np.concatenate(([0.0], np.cumsum(prin)[:-1])), 2)
        raw_int = np.clip(opening, 0, None) * r
        interest = raw_int * (TI / raw_int.sum()) if raw_int.sum() > 0 else np.full(m, TI / m)
        interest = round_cumulative(interest)
        fees = np.zeros(m)
        fees[-1] = late
    else:
        prin = interest = fees = opening = np.zeros(0)

    months = [i0] + list(range(i0 + 1, i0 + 1 + m))
    rec = {
        "month_mi":        months,
        "opening_balance": [0.0] + list(opening),
        "disbursed_amt":   [P] + [0.0] * m,
        "interest_paid":   [0.0] + list(interest),
        "principal_paid":  [0.0] + list(prin),
        "late_fees_paid":  [0.0] + list(fees),
        "charge_off_amt":  [0.0] * (m + 1),
        "recovery_amt":    [0.0] * (m + 1),
        "month_status":    ["Current"] * (m + 1),
    }
    balance = round(P - float(np.sum(prin)), 2)

    def add(mi, open_bal, status_label, charge_off=0.0, recovery=0.0):
        if mi <= rec["month_mi"][-1]:
            # same month as the previous row (event clipped to the snapshot): add to that row
            rec["charge_off_amt"][-1] += charge_off
            rec["recovery_amt"][-1] += recovery
            rec["month_status"][-1] = status_label
            return
        rec["month_mi"].append(mi)
        rec["opening_balance"].append(open_bal)
        for col in ("disbursed_amt", "interest_paid", "principal_paid", "late_fees_paid"):
            rec[col].append(0.0)
        rec["charge_off_amt"].append(charge_off)
        rec["recovery_amt"].append(recovery)
        rec["month_status"].append(status_label)

    # ---- what happens after the last payment -----------------------------------
    if status == "Fully Paid":
        rec["month_status"][-1] = "Paid Off"
    elif status == "Charged Off":
        co = min(last + CHARGE_OFF_LAG, snap)
        for mi in range(last + 1, co):
            add(mi, balance, "Delinquent")
        add(co, balance, "Charged Off", charge_off=balance)
        if net_recovery > 0:
            add(min(co + RECOVERY_LAG, snap), 0.0, "Charged Off", recovery=net_recovery)
        balance = 0.0
    else:  # Current, In Grace Period, Late, Default -> still on the books at the snapshot
        label = "Delinquent" if status in BAD_STATUSES else "Current"
        for mi in range(last + 1, snap + 1):
            add(mi, balance, label)

    # closing balance = opening + disbursed - principal - charge-off
    rec["closing_balance"] = [
        o + d - p - c for o, d, p, c in zip(
            rec["opening_balance"], rec["disbursed_amt"], rec["principal_paid"], rec["charge_off_amt"])
    ]
    rec["loan_id"] = [int(row.id)] * len(rec["month_mi"])
    return rec


def build_fact_loan(book: pd.DataFrame) -> pd.DataFrame:
    fact = pd.DataFrame({
        "loan_id": book["id"].astype("int64"),
        "issue_month": book["issue_d"],
        "term_months": book["term_months"].astype("int64"),
        "grade": book["grade"],
        "sub_grade": book["sub_grade"],
        "purpose": book["purpose"],
        "addr_state": book["addr_state"],
        "loan_amnt": book["loan_amnt"],
        "funded_amnt": book["funded_amnt"],
        "orig_fee_rate": book["grade"].map(ORIG_FEE_BY_GRADE),
        "int_rate": book["int_rate"],
        "installment": book["installment"],
        "annual_inc": book["annual_inc"],
        "fico_score": ((book["fico_range_low"] + book["fico_range_high"]) / 2).round().astype("int64"),
        "loan_status": book["loan_status"],
    })
    fact["orig_fee_amt"] = (fact["funded_amnt"] * fact["orig_fee_rate"]).round(2)
    return fact


def main() -> None:
    start = time.time()
    loans = pd.read_parquet(IN_FILE)
    pool = loans[loans["issue_year"].isin(BOOK_YEARS)]
    book = pool.sample(frac=SAMPLE_FRAC, random_state=RANDOM_SEED).sort_values("issue_d").reset_index(drop=True)
    print(f"Arcadia loan book: {len(book):,} loans sampled from {len(pool):,} ({SAMPLE_FRAC:.0%})")

    parts = []
    for i, row in enumerate(book.itertuples(index=False), start=1):
        parts.append(pd.DataFrame(simulate_loan(row)))
        if i % 10_000 == 0:
            print(f"  simulated {i:,} loans ({time.time() - start:.0f}s)")

    months = pd.concat(parts, ignore_index=True)
    months["month_start"] = from_mi(months.pop("month_mi").to_numpy())
    money = ["opening_balance", "disbursed_amt", "interest_paid", "principal_paid",
             "late_fees_paid", "charge_off_amt", "recovery_amt", "closing_balance"]
    months[money] = months[money].round(2)
    months = months[["loan_id", "month_start"] + money + ["month_status"]]

    fact_loan = build_fact_loan(book)
    OUT_LOAN.parent.mkdir(parents=True, exist_ok=True)
    fact_loan.to_parquet(OUT_LOAN, index=False)
    months.to_parquet(OUT_MONTH, index=False)

    print(f"\nfact_loan       : {len(fact_loan):,} rows -> {OUT_LOAN}")
    print(f"fact_loan_month : {len(months):,} rows -> {OUT_MONTH}")
    print(f"Months covered  : {months['month_start'].min().date()} to {months['month_start'].max().date()}")
    print(f"Originations    : ${fact_loan['funded_amnt'].sum():,.0f}")
    by_year = months.assign(year=months["month_start"].dt.year).groupby("year")[
        ["disbursed_amt", "interest_paid", "charge_off_amt", "recovery_amt"]].sum()
    print("\nYearly totals ($):")
    print((by_year / 1e6).round(2).rename(columns=lambda c: c + " (m)").to_string())
    print(f"\nDone in {time.time() - start:.0f}s")


if __name__ == "__main__":
    main()
