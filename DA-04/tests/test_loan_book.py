"""
Tests for the simulated loan book. Run from the project root:
    pytest -q

Every test here is a reconciliation a finance team would ask for:
"do your monthly numbers add back up to the source system?"
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

SRC = Path("data/processed/loans_2014_2018.parquet")
LOAN = Path("data/processed/fact_loan.parquet")
MONTH = Path("data/processed/fact_loan_month.parquet")
TOL = 0.005  # dollars: monthly amounts use cumulative rounding, so totals must tie to the cent

pytestmark = pytest.mark.skipif(not MONTH.exists(), reason="run src/build_loan_book.py first")


@pytest.fixture(scope="module")
def data():
    loans = pd.read_parquet(LOAN)
    months = pd.read_parquet(MONTH)
    src = pd.read_parquet(SRC).set_index("id").loc[loans["loan_id"]]
    totals = months.groupby("loan_id").sum(numeric_only=True)
    return loans, months, src, totals


def test_one_row_per_loan_per_month(data):
    _, months, _, _ = data
    assert not months.duplicated(["loan_id", "month_start"]).any()


def test_every_loan_has_history(data):
    loans, months, _, _ = data
    assert set(loans["loan_id"]) == set(months["loan_id"])


def test_disbursed_equals_funded(data):
    loans, _, _, totals = data
    diff = totals.loc[loans["loan_id"], "disbursed_amt"].to_numpy() - loans["funded_amnt"].to_numpy()
    assert np.abs(diff).max() < TOL


@pytest.mark.parametrize("col, src_col", [
    ("principal_paid", "total_rec_prncp"),
    ("interest_paid", "total_rec_int"),
    ("late_fees_paid", "total_rec_late_fee"),
])
def test_cash_ties_to_lending_club_totals(data, col, src_col):
    _, _, src, totals = data
    diff = totals.loc[src.index, col].to_numpy() - src[src_col].to_numpy()
    assert np.abs(diff).max() < TOL, f"{col} max diff {np.abs(diff).max():.2f}"


def test_balance_roll_forward_every_row(data):
    _, months, _, _ = data
    expected = (months["opening_balance"] + months["disbursed_amt"]
                - months["principal_paid"] - months["charge_off_amt"])
    assert (expected - months["closing_balance"]).abs().max() < 0.02


def test_opening_equals_prior_closing(data):
    _, months, _, _ = data
    m = months.sort_values(["loan_id", "month_start"])
    prior_close = m.groupby("loan_id")["closing_balance"].shift()
    gap = (m["opening_balance"] - prior_close).abs().dropna()
    assert gap.max() < 0.02


def test_charged_off_loans_end_at_zero(data):
    loans, months, _, _ = data
    co_ids = loans.loc[loans["loan_status"] == "Charged Off", "loan_id"]
    last = months.sort_values("month_start").groupby("loan_id").tail(1).set_index("loan_id")
    assert last.loc[co_ids, "closing_balance"].abs().max() < TOL


def test_open_loans_match_outstanding_principal(data):
    """Loans still open at the snapshot must end at Lending Club's out_prncp."""
    loans, months, src, _ = data
    open_ids = loans.loc[~loans["loan_status"].isin(["Fully Paid", "Charged Off"]), "loan_id"]
    last = months.sort_values("month_start").groupby("loan_id").tail(1).set_index("loan_id")
    diff = last.loc[open_ids, "closing_balance"].to_numpy() - src.loc[open_ids, "out_prncp"].to_numpy()
    # small tolerance: LC's out_prncp and total_rec_prncp occasionally differ by cents
    assert np.abs(diff).max() < 1.0, f"max diff {np.abs(diff).max():.2f}"


def test_no_activity_after_snapshot(data):
    _, months, _, _ = data
    assert months["month_start"].max() <= pd.Timestamp("2019-03-01")


def test_no_negative_balances(data):
    _, months, _, _ = data
    assert (months[["opening_balance", "closing_balance"]] > -TOL).all().all()
