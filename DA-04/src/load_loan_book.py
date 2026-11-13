"""
Step 3 - Load the loan book into MySQL.

Input : data/processed/fact_loan.parquet, fact_loan_month.parquet
Output: arcadia_finance.fact_loan, arcadia_finance.fact_loan_month

Idempotent: it empties both tables first, so you can re-run it safely.
Run from the project root (after the two SQL scripts in sql/):
    python src/load_loan_book.py
"""

from pathlib import Path
import time

import pandas as pd
from sqlalchemy import text

from db import get_engine

LOAN = Path("data/processed/fact_loan.parquet")
MONTH = Path("data/processed/fact_loan_month.parquet")
CHUNK = 20_000


def load(df: pd.DataFrame, table: str, engine) -> None:
    start = time.time()
    for i in range(0, len(df), CHUNK):
        df.iloc[i:i + CHUNK].to_sql(table, engine, if_exists="append", index=False, method="multi")
        print(f"  {table}: {min(i + CHUNK, len(df)):,}/{len(df):,} rows ({time.time() - start:.0f}s)", end="\r")
    print()


def main() -> None:
    engine = get_engine()
    loans = pd.read_parquet(LOAN)
    months = pd.read_parquet(MONTH)
    loans["issue_month"] = loans["issue_month"].dt.date
    months["month_start"] = months["month_start"].dt.date

    with engine.begin() as conn:
        # child table first, because of the foreign key
        conn.execute(text("DELETE FROM fact_loan_month"))
        conn.execute(text("DELETE FROM fact_loan"))

    load(loans, "fact_loan", engine)
    load(months, "fact_loan_month", engine)

    with engine.connect() as conn:
        n_loan = conn.execute(text("SELECT COUNT(*) FROM fact_loan")).scalar()
        n_month = conn.execute(text("SELECT COUNT(*) FROM fact_loan_month")).scalar()
    assert n_loan == len(loans) and n_month == len(months), "row counts do not match the parquet files"
    print(f"Loaded fact_loan={n_loan:,} and fact_loan_month={n_month:,} rows. Counts match source.")


if __name__ == "__main__":
    main()
