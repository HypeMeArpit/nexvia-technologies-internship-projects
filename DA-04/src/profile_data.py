"""
Step 1 - Profile the Lending Club accepted-loans file and save a working subset.

What this does:
  1. Reads the 1.6 GB file in chunks (so it doesn't crash your RAM), keeping only
     the columns we need.
  2. Prints a profile: loans by issue year x term x status, missing values, basic stats.
  3. Saves loans issued 2014-2018 to data/processed/loans_2014_2018.parquet.
     - 2014-2015 36-month loans  -> train / out-of-time test the credit scorecard
     - 2016-2018 loans           -> "Arcadia Lending" loan book for the P&L and balance sheet

Run from the project root:
    python src/profile_data.py
"""

from pathlib import Path
import time

import pandas as pd

RAW_FILE = Path("data/raw/accepted_2007_to_2018Q4.csv.gz")
OUT_FILE = Path("data/processed/loans_2014_2018.parquet")
KEEP_YEARS = range(2014, 2019)  # 2014, 2015, 2016, 2017, 2018

# Columns grouped by purpose. Keeping the grouping makes leakage rules explicit later.
ID_COLS = ["id"]
APPLICATION_COLS = [  # known at the moment the loan is approved -> allowed in the scorecard
    "loan_amnt", "term", "int_rate", "installment", "grade", "sub_grade",
    "emp_length", "home_ownership", "annual_inc", "verification_status",
    "purpose", "addr_state", "dti", "delinq_2yrs", "earliest_cr_line",
    "fico_range_low", "fico_range_high", "inq_last_6mths", "open_acc",
    "pub_rec", "revol_bal", "revol_util", "total_acc", "mort_acc",
    "pub_rec_bankruptcies",
]
OUTCOME_COLS = [  # known only AFTER the loan is issued -> used for financials, NEVER as model inputs
    "issue_d", "loan_status", "funded_amnt", "out_prncp", "total_pymnt",
    "total_rec_prncp", "total_rec_int", "total_rec_late_fee", "recoveries",
    "collection_recovery_fee", "last_pymnt_d", "last_pymnt_amnt",
]
USE_COLS = ID_COLS + APPLICATION_COLS + OUTCOME_COLS


def load_subset() -> pd.DataFrame:
    if not RAW_FILE.exists():
        raise FileNotFoundError(
            f"{RAW_FILE} not found. Download it from Kaggle (wordsforthewise/lending-club) "
            "and place it in data/raw/."
        )

    parts, total_rows = [], 0
    start = time.time()
    reader = pd.read_csv(RAW_FILE, usecols=USE_COLS, chunksize=200_000, low_memory=False)
    for i, chunk in enumerate(reader, start=1):
        total_rows += len(chunk)
        # The file ends with a few summary rows that have no valid issue date - drop them.
        chunk["issue_d"] = pd.to_datetime(chunk["issue_d"], format="%b-%Y", errors="coerce")
        chunk = chunk[chunk["issue_d"].dt.year.isin(KEEP_YEARS)]
        parts.append(chunk)
        print(f"  chunk {i}: read {total_rows:,} rows so far ({time.time() - start:.0f}s)")

    df = pd.concat(parts, ignore_index=True)
    print(f"\nRead {total_rows:,} rows in total; kept {len(df):,} loans issued 2014-2018.")
    return df


def clean_types(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["id"] = pd.to_numeric(df["id"], errors="coerce").astype("Int64")
    df["term_months"] = df["term"].str.extract(r"(\d+)").astype("Int64")
    # int_rate / revol_util can arrive as '13.5%' strings or numbers depending on the file version
    for col in ["int_rate", "revol_util"]:
        if df[col].dtype == object:
            df[col] = pd.to_numeric(df[col].str.rstrip("%"), errors="coerce")
    df["last_pymnt_d"] = pd.to_datetime(df["last_pymnt_d"], format="%b-%Y", errors="coerce")
    df["earliest_cr_line"] = pd.to_datetime(df["earliest_cr_line"], format="%b-%Y", errors="coerce")
    df["issue_year"] = df["issue_d"].dt.year
    return df


def profile(df: pd.DataFrame) -> None:
    line = "=" * 70
    print(f"\n{line}\nLOANS BY ISSUE YEAR x TERM\n{line}")
    print(pd.crosstab(df["issue_year"], df["term_months"], margins=True))

    print(f"\n{line}\nLOAN STATUS BY ISSUE YEAR (row %)\n{line}")
    print((pd.crosstab(df["issue_year"], df["loan_status"], normalize="index") * 100).round(1))

    print(f"\n{line}\nGRADE MIX BY ISSUE YEAR (row %)\n{line}")
    print((pd.crosstab(df["issue_year"], df["grade"], normalize="index") * 100).round(1))

    print(f"\n{line}\nMISSING VALUES (% of rows, only columns with any missing)\n{line}")
    missing = (df.isna().mean() * 100).round(2)
    print(missing[missing > 0].sort_values(ascending=False).to_string())

    print(f"\n{line}\nKEY NUMERIC STATS\n{line}")
    cols = ["loan_amnt", "int_rate", "annual_inc", "dti", "fico_range_low", "revol_util"]
    print(df[cols].describe().T.round(2).to_string())

    print(f"\n{line}\nDATE RANGE CHECKS\n{line}")
    print("issue_d      :", df["issue_d"].min().date(), "->", df["issue_d"].max().date())
    print("last_pymnt_d :", df["last_pymnt_d"].min(), "->", df["last_pymnt_d"].max())
    print("duplicate ids:", df["id"].duplicated().sum())


def main() -> None:
    df = clean_types(load_subset())
    profile(df)
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT_FILE, index=False)
    print(f"\nSaved {len(df):,} rows x {df.shape[1]} columns to {OUT_FILE}")


if __name__ == "__main__":
    main()
