"""
Tests for the general ledger and budget. Run from the project root:
    pytest -q

These mirror the controls in sql/05_reconcile_reports.sql, but run on the parquet
files so they work without a database (e.g. in CI).
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

DATA = Path("data/processed")
pytestmark = pytest.mark.skipif(not (DATA / "fact_gl.parquet").exists(),
                                reason="run src/build_ledger.py first")
CENT = 0.01


@pytest.fixture(scope="module")
def gl():
    df = pd.read_parquet(DATA / "fact_gl.parquet")
    df["net_debit"] = df["debit"] - df["credit"]
    return df


@pytest.fixture(scope="module")
def balances(gl):
    """Month-end balance (debit positive) for every account."""
    moves = gl.pivot_table(index="month_start", columns="account_code", values="net_debit",
                           aggfunc="sum", fill_value=0.0)
    return moves.cumsum()


@pytest.fixture(scope="module")
def book():
    months = pd.read_parquet(DATA / "fact_loan_month.parquet")
    return months[months["month_start"] <= "2018-12-01"].groupby("month_start").sum(numeric_only=True)


def test_every_journal_balances(gl):
    by_journal = gl.groupby("journal_id")["net_debit"].sum()
    assert by_journal.abs().max() < CENT


def test_each_line_is_one_sided(gl):
    assert not ((gl["debit"] > 0) & (gl["credit"] > 0)).any()
    assert (gl[["debit", "credit"]] >= 0).all().all()


def test_balance_sheet_balances_every_month(balances):
    """Assets = Liabilities + Equity + retained earnings, i.e. all balances net to zero."""
    assert balances.sum(axis=1).abs().max() < CENT


def test_gl_loans_equal_loan_book(balances, book):
    diff = balances[1100] - book["closing_balance"].reindex(balances.index)
    assert diff.abs().max() < CENT


def test_gl_interest_equals_loan_book(gl, book):
    gl_int = -gl[gl["account_code"] == 4000].groupby("month_start")["net_debit"].sum()
    diff = gl_int - book["interest_paid"].reindex(gl_int.index)
    assert diff.abs().max() < CENT


def test_cash_never_negative(balances):
    assert balances[1000].min() >= -CENT


def test_allowance_is_a_credit_balance(balances):
    assert balances[1150].max() <= CENT


def test_budget_covers_fy2018(gl):
    budget = pd.read_parquet(DATA / "fact_budget.parquet")
    assert budget["month_start"].dt.year.eq(2018).all()
    assert budget["month_start"].nunique() == 12
    pl_accounts = set(gl.loc[gl["account_code"] >= 4000, "account_code"])
    assert pl_accounts <= set(budget["account_code"]), "every P&L account needs a budget"


def test_interest_variance_drivers_add_up():
    """volume + mix + rate must equal actual - budget interest, every month."""
    drv = pd.read_parquet(DATA / "fact_budget_driver.parquet")
    loans = pd.read_parquet(DATA / "fact_loan.parquet")[["loan_id", "grade"]]
    months = pd.read_parquet(DATA / "fact_loan_month.parquet")
    act = (months[months["month_start"].isin(drv["month_start"])].merge(loans, on="loan_id")
           .groupby(["month_start", "grade"])[["opening_balance", "interest_paid"]].sum().reset_index())
    g = drv.merge(act, on=["month_start", "grade"], how="left").fillna(0)
    g["y"] = g["planned_yield"] / 12
    g["bud_int"] = g["planned_opening_bal"] * g["y"]
    for _, m in g.groupby("month_start"):
        A, B = m["opening_balance"].sum(), m["planned_opening_bal"].sum()
        volume = (A - B) * m["bud_int"].sum() / B
        mix = ((m["opening_balance"] - A * m["planned_opening_bal"] / B) * m["y"]).sum()
        rate = (m["interest_paid"] - m["opening_balance"] * m["y"]).sum()
        total = m["interest_paid"].sum() - m["bud_int"].sum()
        assert abs(volume + mix + rate - total) < CENT


def test_kpi_drivers_present():
    kpi = pd.read_parquet(DATA / "fact_kpi_driver.parquet")
    fy18 = kpi[kpi["month_start"].dt.year == 2018]
    assert fy18["budget_value"].notna().all()
    assert set(kpi["kpi_code"]) == {"HEADCOUNT", "COST_PER_HEAD", "NEW_LOANS", "CAC",
                                    "ORIGINATIONS", "FUNDING_RATE", "OPENING_BORROWINGS"}
