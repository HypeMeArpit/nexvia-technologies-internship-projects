"""
Step 4 - Credit risk scorecard (Weight of Evidence + logistic regression) and risk bands.

Input : data/processed/loans_2014_2018.parquet   (application data + outcomes)
        data/processed/fact_loan.parquet         (Arcadia's book, from build_loan_book.py)
Output: models/scorecard.json                    bins, WoE, points, scaling: everything needed to score
        reports/scorecard/*.csv, *.png           IV table, points table, validation, risk bands, charts
        data/processed/fact_loan.parquet         + score_points, pd_12m, risk_band for every Arcadia loan

METHOD (standard retail-credit scorecard)
  1. Population : 36-month loans issued 2014 (development) and 2015 (out-of-time test).
                  Bad = Charged Off / Default. 2014 is split 70/30 into train / validation.
  2. Features   : ONLY information known on the application date (no leakage).
                  Lending Club's own grade, sub-grade and interest rate are EXCLUDED: they are
                  Lending Club's risk assessment, so using them would copy their model. They are
                  kept as a benchmark instead. State of residence is excluded too (geographic proxy).
  3. Binning    : fine quantile bins -> merged until each bin has >= 5% of loans and the bad
                  rate moves in one direction (monotonic), so every bin makes business sense.
  4. WoE / IV   : WoE = ln(%good / %bad) per bin; Information Value ranks predictive power.
  5. Selection  : IV >= 0.02, drop features correlated > 0.6 with a stronger one, drop any
                  feature whose coefficient has the wrong sign.
  6. Model      : logistic regression on the WoE values.
  7. Scaling    : 600 points = odds of 50 good : 1 bad; every 20 points doubles the odds (PDO).
  8. Calibration: ranking learned on 2014; the average PD is re-levelled to the 2015 default rate
                  (one intercept shift), because later vintages default more.
  9. Bands      : six risk bands by score.

Run from the DA-04 folder:
    python src/credit_scorecard.py
"""

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # draw charts to files, no window
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, roc_curve
from sklearn.model_selection import train_test_split

DATA = Path("data/processed")
MODEL_FILE = Path("models/scorecard.json")
OUT = Path("reports/scorecard")
SEED = 42

BAD_STATUSES = {"Charged Off", "Default"}
NUMERIC = ["fico", "annual_inc", "dti", "loan_amnt", "loan_to_income", "revol_util", "revol_bal",
           "inq_last_6mths", "delinq_2yrs", "pub_rec", "pub_rec_bankruptcies", "open_acc",
           "total_acc", "mort_acc", "credit_history_months", "emp_length_years"]
CATEGORICAL = ["home_ownership", "verification_status", "purpose"]
BENCHMARK_ONLY = ["grade", "sub_grade", "int_rate"]

MIN_BIN_SHARE = 0.05
IV_MIN = 0.02
MAX_CORR = 0.6
BASE_SCORE, BASE_ODDS, PDO = 600, 50, 20
FACTOR = PDO / np.log(2)
OFFSET = BASE_SCORE - FACTOR * np.log(BASE_ODDS)
BANDS = ["R1 Very low", "R2 Low", "R3 Moderate", "R4 Elevated", "R5 High", "R6 Very high"]


# =====================================================================================
# Features
# =====================================================================================
def make_features(df: pd.DataFrame) -> pd.DataFrame:
    """Application-time features only."""
    X = pd.DataFrame(index=df.index)
    X["fico"] = (df["fico_range_low"] + df["fico_range_high"]) / 2
    X["annual_inc"] = df["annual_inc"].where(df["annual_inc"] > 0)            # 0 income -> missing
    X["dti"] = df["dti"].where((df["dti"] >= 0) & (df["dti"] < 100))          # -1 / 999 -> missing
    X["loan_amnt"] = df["loan_amnt"]
    X["loan_to_income"] = df["loan_amnt"] / X["annual_inc"]
    X["revol_util"] = df["revol_util"].clip(upper=150)
    for c in ["revol_bal", "inq_last_6mths", "delinq_2yrs", "pub_rec", "pub_rec_bankruptcies",
              "open_acc", "total_acc", "mort_acc"]:
        X[c] = df[c]
    months = (df["issue_d"].dt.year - df["earliest_cr_line"].dt.year) * 12 + \
             (df["issue_d"].dt.month - df["earliest_cr_line"].dt.month)
    X["credit_history_months"] = months
    X["emp_length_years"] = (df["emp_length"].str.replace("< 1 year", "0")
                             .str.extract(r"(\d+)")[0].astype(float))
    X["home_ownership"] = df["home_ownership"].replace({"ANY": "OTHER", "NONE": "OTHER"})
    X["verification_status"] = df["verification_status"]
    X["purpose"] = df["purpose"]
    return X


# =====================================================================================
# Binning and Weight of Evidence
# =====================================================================================
def woe_table(groups: pd.Series, bad: pd.Series) -> pd.DataFrame:
    t = pd.DataFrame({"bin": groups, "bad": bad}).groupby("bin", observed=True)["bad"].agg(["count", "sum"])
    t.columns = ["n", "bads"]
    t["goods"] = t["n"] - t["bads"]
    G, B = t["goods"].sum(), t["bads"].sum()
    # +0.5 smoothing avoids log(0) for a bin with no goods or no bads
    t["woe"] = np.log(((t["goods"] + 0.5) / G) / ((t["bads"] + 0.5) / B))
    t["iv"] = (t["goods"] / G - t["bads"] / B) * t["woe"]
    t["bad_rate"] = t["bads"] / t["n"]
    t["share"] = t["n"] / t["n"].sum()
    return t


def bin_numeric(x: pd.Series, bad: pd.Series) -> list[float]:
    """Return interior cut points. Bins: (-inf, c1], (c1, c2], ..., (ck, inf); missing separate."""
    valid = x.notna()
    xs, ys = x[valid], bad[valid]
    cuts = list(np.unique(np.quantile(xs, np.linspace(0.05, 0.95, 19))))
    xr = pd.Series(xs.to_numpy(dtype=float)).rank()
    yr = pd.Series(ys.to_numpy(dtype=float)).rank()
    direction = np.sign(xr.corr(yr)) or 1.0

    def table(c):
        g = pd.cut(xs, [-np.inf] + c + [np.inf], right=True)
        t = pd.DataFrame({"g": g, "y": ys}).groupby("g", observed=False)["y"].agg(["count", "mean"])
        return t["count"].to_numpy(), t["mean"].to_numpy()

    changed = True
    while changed and cuts:
        changed = False
        n, rate = table(cuts)
        # 1) every bin must hold at least MIN_BIN_SHARE of loans: merge the smallest with a neighbour
        small = np.where(n < MIN_BIN_SHARE * n.sum())[0]
        if len(small):
            i = small[np.argmin(n[small])]
            if i == 0:
                drop = 0
            elif i == len(n) - 1:
                drop = len(cuts) - 1
            else:  # merge with the neighbour whose bad rate is closer
                drop = i - 1 if abs(rate[i] - rate[i - 1]) <= abs(rate[i] - rate[i + 1]) else i
            cuts.pop(drop)
            changed = True
            continue
        # 2) bad rate must move in one direction: merge the first pair that breaks the trend
        for i in range(len(rate) - 1):
            if (rate[i + 1] - rate[i]) * direction < 0:
                cuts.pop(i)
                changed = True
                break
    return [float(c) for c in cuts]


def assign_numeric(x: pd.Series, cuts: list[float]) -> pd.Series:
    labels = bin_labels(cuts)
    idx = np.searchsorted(np.array(cuts), x.to_numpy(), side="left")  # x <= c1 -> 0, ...
    out = pd.Series(np.array(labels, dtype=object)[np.minimum(idx, len(labels) - 1)], index=x.index)
    return out.where(x.notna(), "Missing")


def bin_labels(cuts: list[float]) -> list[str]:
    fmt = lambda v: f"{v:,.4g}"
    if not cuts:
        return ["All"]
    labels = [f"<= {fmt(cuts[0])}"]
    labels += [f"({fmt(a)}, {fmt(b)}]" for a, b in zip(cuts[:-1], cuts[1:])]
    labels.append(f"> {fmt(cuts[-1])}")
    return labels


def bin_categorical(x: pd.Series) -> dict[str, str]:
    """Rare categories (< 1% of loans) are grouped as OTHER."""
    share = x.fillna("Missing").value_counts(normalize=True)
    return {cat: (cat if s >= 0.01 else "OTHER") for cat, s in share.items()}


def assign_categorical(x: pd.Series, mapping: dict[str, str]) -> pd.Series:
    return x.fillna("Missing").map(mapping).fillna("OTHER")


def fit_bins(X: pd.DataFrame, y: pd.Series) -> dict:
    spec = {}
    for f in NUMERIC:
        spec[f] = {"type": "numeric", "cuts": bin_numeric(X[f], y)}
    for f in CATEGORICAL:
        spec[f] = {"type": "categorical", "map": bin_categorical(X[f])}
    return spec


def apply_bins(X: pd.DataFrame, spec: dict) -> pd.DataFrame:
    out = pd.DataFrame(index=X.index)
    for f, s in spec.items():
        out[f] = assign_numeric(X[f], s["cuts"]) if s["type"] == "numeric" else assign_categorical(X[f], s["map"])
    return out


# =====================================================================================
# Metrics
# =====================================================================================
def ks_stat(y, score):
    """Max gap between the cumulative score distributions of bads and goods.
    Uses roc_curve so loans with tied scores are grouped together; sorting row by row
    made the result depend on the order of ties, which differs between computers."""
    fpr, tpr, _ = roc_curve(y, score)
    return float(np.max(tpr - fpr))


def psi(expected: pd.Series, actual: pd.Series) -> float:
    """Population Stability Index between two share distributions over the same categories."""
    e, a = expected.align(actual, fill_value=0)
    e, a = e.clip(lower=1e-6), a.clip(lower=1e-6)
    return float(((a - e) * np.log(a / e)).sum())


def metrics(name, y, score_bad_prob):
    auc = roc_auc_score(y, score_bad_prob)
    return {"sample": name, "loans": int(len(y)), "bad_rate": round(float(np.mean(y)), 4),
            "auc": round(auc, 4), "gini": round(2 * auc - 1, 4), "ks": round(ks_stat(y, score_bad_prob), 4)}


# =====================================================================================
# Main
# =====================================================================================
def main():
    OUT.mkdir(parents=True, exist_ok=True)
    MODEL_FILE.parent.mkdir(parents=True, exist_ok=True)

    src = pd.read_parquet(DATA / "loans_2014_2018.parquet")
    dev = src[(src["term_months"] == 36) & src["issue_year"].isin([2014, 2015])
              & src["loan_status"].isin(BAD_STATUSES | {"Fully Paid"})].copy()
    dev["bad"] = dev["loan_status"].isin(BAD_STATUSES).astype(int)
    X_all = make_features(dev)

    is14 = dev["issue_year"] == 2014
    train_idx, valid_idx = train_test_split(dev.index[is14], test_size=0.3, random_state=SEED,
                                            stratify=dev.loc[is14, "bad"])
    oot_idx = dev.index[~is14]
    y = dev["bad"]
    print(f"Train {len(train_idx):,} | validation {len(valid_idx):,} | out-of-time (2015) {len(oot_idx):,}")

    # ---- binning + WoE on TRAIN only ------------------------------------------------
    spec = fit_bins(X_all.loc[train_idx], y.loc[train_idx])
    B_all = apply_bins(X_all, spec)
    tables, iv = {}, {}
    for f in spec:
        t = woe_table(B_all.loc[train_idx, f], y.loc[train_idx])
        tables[f], iv[f] = t, t["iv"].sum()
    iv_df = (pd.DataFrame({"feature": list(iv), "iv": list(iv.values())})
             .sort_values("iv", ascending=False).reset_index(drop=True))
    iv_df["strength"] = pd.cut(iv_df["iv"], [-1, 0.02, 0.1, 0.3, 0.5, 99],
                               labels=["Not useful", "Weak", "Medium", "Strong", "Suspicious"])

    def to_woe(bins: pd.DataFrame, feats) -> pd.DataFrame:
        return pd.DataFrame({f: bins[f].map(tables[f]["woe"]).fillna(0.0) for f in feats}, index=bins.index)

    # ---- feature selection ------------------------------------------------------------
    candidates = iv_df.loc[(iv_df["iv"] >= IV_MIN) & (iv_df["iv"] < 0.5), "feature"].tolist()
    W_train = to_woe(B_all.loc[train_idx], candidates)
    selected = []
    for f in candidates:  # strongest first; skip if too correlated with one already kept
        if all(abs(W_train[f].corr(W_train[k])) <= MAX_CORR for k in selected):
            selected.append(f)
    while True:
        model = LogisticRegression(C=1e6, max_iter=2000)
        model.fit(W_train[selected], y.loc[train_idx])
        coefs = pd.Series(model.coef_[0], index=selected)
        wrong = coefs[coefs > 0]  # higher WoE = safer, so every coefficient must be negative
        if wrong.empty:
            break
        selected.remove(wrong.idxmax())
    iv_df["selected"] = iv_df["feature"].isin(selected)
    print("Selected features:", selected)

    # ---- scorecard points ----------------------------------------------------------------
    alpha, n = float(model.intercept_[0]), len(selected)
    rows = []
    for f in selected:
        t = tables[f]
        for b, r in t.iterrows():
            pts = -coefs[f] * r["woe"] * FACTOR + (OFFSET - FACTOR * alpha) / n
            rows.append({"feature": f, "bin": b, "loans": int(r["n"]), "share": round(r["share"], 4),
                         "bad_rate": round(r["bad_rate"], 4), "woe": round(r["woe"], 4),
                         "points": int(round(pts))})
    points = pd.DataFrame(rows)
    point_map = {f: dict(zip(g["bin"], g["points"])) for f, g in points.groupby("feature")}
    # unseen/empty bin at scoring time -> neutral points (WoE 0)
    neutral = {f: int(round((OFFSET - FACTOR * alpha) / n)) for f in selected}

    def score(bins: pd.DataFrame) -> pd.Series:
        return sum(bins[f].map(point_map[f]).fillna(neutral[f]) for f in selected).astype(int)

    dev["score"] = score(B_all)
    # model PD implied by the score (before calibration)
    dev["pd_model"] = 1 / (1 + np.exp((dev["score"] - OFFSET) / FACTOR))

    # ---- calibration: shift the log-odds so the average PD matches 2015's default rate ----
    target = y.loc[oot_idx].mean()
    logit = np.log(dev.loc[oot_idx, "pd_model"] / (1 - dev.loc[oot_idx, "pd_model"]))
    lo, hi = -3.0, 3.0
    for _ in range(60):  # bisection: find shift so mean(sigmoid(logit + shift)) == target
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if (1 / (1 + np.exp(-(logit + mid)))).mean() < target else (lo, mid)
    shift = (lo + hi) / 2
    pd_from_score = lambda s: 1 / (1 + np.exp((s - OFFSET) / FACTOR - shift))
    dev["pd_36m"] = pd_from_score(dev["score"])

    # ---- validation ---------------------------------------------------------------------
    val = [metrics("Train (2014, 70%)", y.loc[train_idx], -dev.loc[train_idx, "score"]),
           metrics("Validation (2014, 30%)", y.loc[valid_idx], -dev.loc[valid_idx, "score"]),
           metrics("Out-of-time (2015)", y.loc[oot_idx], -dev.loc[oot_idx, "score"])]
    order = sorted(dev["sub_grade"].dropna().unique())  # A1, A2 ... G5: higher = riskier
    sub_rank = dev["sub_grade"].map({g: i for i, g in enumerate(order)})
    val.append(metrics("Benchmark: Lending Club sub-grade (2015)", y.loc[oot_idx], sub_rank.loc[oot_idx]))
    val_df = pd.DataFrame(val)

    # ---- risk bands: cut-offs at train score percentiles 10/30/50/70/90, rounded to 5 ------
    q = np.percentile(dev.loc[train_idx, "score"], [10, 30, 50, 70, 90])
    cutoffs = [int(5 * round(v / 5)) for v in q]  # ascending score cut-offs

    def band(s: pd.Series) -> pd.Series:
        idx = np.searchsorted(cutoffs, s.to_numpy(), side="right")  # 0 = lowest scores
        return pd.Series(np.array(BANDS[::-1], dtype=object)[idx], index=s.index)

    dev["risk_band"] = band(dev["score"])
    lows = [None] + cutoffs
    highs = [c - 1 for c in cutoffs] + [None]
    band_rows = []
    for i, name in enumerate(BANDS[::-1]):  # R6 (lowest scores) ... R1
        tr = dev.loc[train_idx][dev.loc[train_idx, "risk_band"] == name]
        oo = dev.loc[oot_idx][dev.loc[oot_idx, "risk_band"] == name]
        band_rows.append({"risk_band": name,
                          "score_from": lows[i] if lows[i] is not None else int(dev["score"].min()),
                          "score_to": highs[i] if highs[i] is not None else int(dev["score"].max()),
                          "train_share": round(len(tr) / len(train_idx), 4),
                          "train_bad_rate": round(tr["bad"].mean(), 4),
                          "oot_bad_rate": round(oo["bad"].mean(), 4),
                          "avg_pd_36m": round(oo["pd_36m"].mean(), 4)})
    bands = pd.DataFrame(band_rows[::-1]).reset_index(drop=True)  # R1 first

    # ---- stability: score-band mix in 2015 vs train -----------------------------------
    share_train = dev.loc[train_idx, "risk_band"].value_counts(normalize=True)
    share_oot = dev.loc[oot_idx, "risk_band"].value_counts(normalize=True)
    stability = {"psi_train_vs_2015": round(psi(share_train, share_oot), 4)}

    # ---- score Arcadia's loan book ------------------------------------------------------
    book = pd.read_parquet(DATA / "fact_loan.parquet")
    feats = make_features(src.set_index("id").loc[book["loan_id"]].reset_index(drop=True))
    feats.index = book.index
    book_bins = apply_bins(feats, spec)
    book["score_points"] = score(book_bins)
    pd36 = pd_from_score(book["score_points"])
    book["pd_12m"] = (1 - (1 - pd36) ** (1 / 3)).round(6)   # 36-month PD -> annual PD
    book["risk_band"] = band(book["score_points"])
    book.to_parquet(DATA / "fact_loan.parquet", index=False)
    stability["psi_train_vs_arcadia_book"] = round(
        psi(share_train, book["risk_band"].value_counts(normalize=True)), 4)

    # outcomes observed so far on Arcadia's book, by band (should rise from R1 to R6)
    book_bad = book["loan_status"].isin(BAD_STATUSES)
    bands["arcadia_loans"] = bands["risk_band"].map(book["risk_band"].value_counts()).fillna(0).astype(int)
    bands["arcadia_bad_rate_to_date"] = bands["risk_band"].map(
        book.assign(b=book_bad).groupby("risk_band")["b"].mean()).round(4)

    # ---- save model + reports ------------------------------------------------------------
    model_json = {
        "description": "Arcadia Lending application scorecard (WoE + logistic regression)",
        "population": "36-month Lending Club loans; train 2014 (70%), validation 2014 (30%), out-of-time 2015",
        "scaling": {"base_score": BASE_SCORE, "base_odds_good_to_bad": BASE_ODDS, "pdo": PDO,
                    "factor": FACTOR, "offset": OFFSET},
        "intercept": alpha, "coefficients": coefs.to_dict(), "calibration_logit_shift": shift,
        "bins": {f: spec[f] for f in selected},
        "points": {f: {str(k): int(v) for k, v in m.items()} for f, m in point_map.items()},
        "neutral_points": neutral, "band_cutoffs_ascending": cutoffs, "bands_low_to_high_score": BANDS[::-1],
        "pd_formula": "pd_36m = 1 / (1 + exp((score - offset) / factor - calibration_logit_shift)); "
                      "pd_12m = 1 - (1 - pd_36m) ** (1/3)",
    }
    MODEL_FILE.write_text(json.dumps(model_json, indent=2))
    iv_df.to_csv(OUT / "iv_summary.csv", index=False)
    points.to_csv(OUT / "scorecard_points.csv", index=False)
    val_df.to_csv(OUT / "validation_metrics.csv", index=False)
    bands.to_csv(OUT / "risk_bands.csv", index=False)
    pd.Series(stability).to_csv(OUT / "stability_psi.csv", header=["psi"])
    save_charts(dev, y, train_idx, oot_idx, bands)

    print("\n" + iv_df.to_string(index=False))
    print("\n" + val_df.to_string(index=False))
    print("\n" + bands.to_string(index=False))
    print("\nStability:", stability, f"| calibration shift {shift:+.3f}")
    print(f"\nScored {len(book):,} Arcadia loans -> {DATA / 'fact_loan.parquet'}")


def save_charts(dev, y, train_idx, oot_idx, bands):
    plt.rcParams.update({"figure.dpi": 150, "axes.spines.top": False, "axes.spines.right": False})

    fig, ax = plt.subplots(figsize=(5.5, 5))
    for idx, label in [(train_idx, "Train 2014"), (oot_idx, "Out-of-time 2015")]:
        fpr, tpr, _ = roc_curve(y.loc[idx], -dev.loc[idx, "score"])
        auc = roc_auc_score(y.loc[idx], -dev.loc[idx, "score"])
        ax.plot(fpr, tpr, label=f"{label} (AUC {auc:.3f})")
    ax.plot([0, 1], [0, 1], linestyle="--", color="grey", linewidth=1)
    ax.set(xlabel="False positive rate", ylabel="True positive rate", title="Scorecard ROC curve")
    ax.legend(loc="lower right", frameon=False)
    fig.tight_layout()
    fig.savefig(OUT / "roc_curve.png")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 4))
    x = np.arange(len(bands))
    ax.bar(x - 0.2, bands["train_bad_rate"] * 100, width=0.4, label="Train 2014")
    ax.bar(x + 0.2, bands["oot_bad_rate"] * 100, width=0.4, label="Out-of-time 2015")
    ax.set_xticks(x, bands["risk_band"], rotation=20)
    ax.set(ylabel="Bad rate (%)", title="Default rate by risk band")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(OUT / "bad_rate_by_band.png")
    plt.close(fig)


if __name__ == "__main__":
    main()
