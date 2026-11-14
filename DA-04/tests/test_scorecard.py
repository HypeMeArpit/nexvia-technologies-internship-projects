"""
Tests for the credit scorecard. Run from the DA-04 folder:
    pytest -q

What a model reviewer (model risk / validation team) would check:
no leakage, stable performance on unseen data, bands that rank risk, every loan scored,
and that the published points table really reproduces the scores.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path("src")))
import credit_scorecard as cs  # noqa: E402

MODEL = Path("models/scorecard.json")
REPORTS = Path("reports/scorecard")
pytestmark = pytest.mark.skipif(not MODEL.exists(), reason="run src/credit_scorecard.py first")

# Columns only known AFTER a loan is issued, plus Lending Club's own risk grading.
FORBIDDEN = {"loan_status", "total_pymnt", "total_rec_prncp", "total_rec_int", "recoveries",
             "out_prncp", "last_pymnt_d", "last_pymnt_amnt", "total_rec_late_fee",
             "collection_recovery_fee", "grade", "sub_grade", "int_rate", "installment", "addr_state"}


@pytest.fixture(scope="module")
def model():
    return json.loads(MODEL.read_text())


@pytest.fixture(scope="module")
def validation():
    return pd.read_csv(REPORTS / "validation_metrics.csv").set_index("sample")


@pytest.fixture(scope="module")
def bands():
    return pd.read_csv(REPORTS / "risk_bands.csv")


def test_no_leakage(model):
    features = set(model["coefficients"])
    assert features <= set(cs.NUMERIC + cs.CATEGORICAL)
    assert not features & FORBIDDEN


def test_coefficients_have_expected_sign(model):
    # WoE = ln(good/bad): higher WoE = safer, so coefficients on P(bad) must be negative
    assert all(c < 0 for c in model["coefficients"].values())


def test_performance_on_unseen_data(validation):
    oot = validation.loc["Out-of-time (2015)"]
    train = validation.loc["Train (2014, 70%)"]
    assert oot["auc"] >= 0.62, "model must rank risk meaningfully better than random (0.5)"
    assert abs(train["auc"] - oot["auc"]) < 0.03, "big train/test gap would mean overfitting"


def test_bad_rate_rises_from_safest_to_riskiest_band(bands):
    for col in ["train_bad_rate", "oot_bad_rate", "avg_pd_36m", "arcadia_bad_rate_to_date"]:
        assert bands[col].is_monotonic_increasing, col  # file is ordered R1 -> R6


def test_population_is_stable():
    psi = pd.read_csv(REPORTS / "stability_psi.csv", index_col=0)["psi"]
    assert (psi < 0.1).all(), "PSI >= 0.1 means the population shifted; the model needs review"


def test_every_arcadia_loan_is_scored(model):
    book = pd.read_parquet("data/processed/fact_loan.parquet")
    assert book["score_points"].notna().all()
    assert book["pd_12m"].between(0, 1, inclusive="neither").all()
    assert set(book["risk_band"]) <= set(model["bands_low_to_high_score"])


def test_points_table_reproduces_scores(model):
    """Rebuild 2,000 scores from scorecard.json alone and compare with what was stored."""
    book = pd.read_parquet("data/processed/fact_loan.parquet").sample(2000, random_state=1)
    src = pd.read_parquet("data/processed/loans_2014_2018.parquet").set_index("id")
    X = cs.make_features(src.loc[book["loan_id"]].reset_index(drop=True))
    X.index = book.index
    bins = cs.apply_bins(X, model["bins"])
    total = sum(bins[f].astype(str).map(model["points"][f]).fillna(model["neutral_points"][f])
                for f in model["points"])
    assert (total.astype(int) == book["score_points"]).all()


def test_pd_formula_matches_stored_pd(model):
    book = pd.read_parquet("data/processed/fact_loan.parquet")
    s = model["scaling"]
    pd36 = 1 / (1 + np.exp((book["score_points"] - s["offset"]) / s["factor"]
                           - model["calibration_logit_shift"]))
    pd12 = 1 - (1 - pd36) ** (1 / 3)
    assert np.abs(pd12 - book["pd_12m"]).max() < 1e-5
