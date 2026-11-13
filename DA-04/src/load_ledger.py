"""
Step 5 - Load the general ledger, budget and driver tables into MySQL.

Input : data/processed/fact_gl.parquet, fact_budget.parquet,
        fact_budget_driver.parquet, fact_kpi_driver.parquet   (from build_ledger.py)
Idempotent: empties the tables first, so it is safe to re-run.

Run from the project root:
    python src/load_ledger.py
"""

from pathlib import Path

import pandas as pd
from sqlalchemy import text

from db import get_engine

DATA = Path("data/processed")
TABLES = ["fact_gl", "fact_budget", "fact_budget_driver", "fact_kpi_driver"]


def main() -> None:
    engine = get_engine()
    with engine.begin() as conn:
        for table in TABLES:
            conn.execute(text(f"DELETE FROM {table}"))

    for table in TABLES:
        df = pd.read_parquet(DATA / f"{table}.parquet")
        df["month_start"] = df["month_start"].dt.date
        df = df.astype(object).where(df.notna(), None)  # NaN -> NULL
        df.to_sql(table, engine, if_exists="append", index=False, method="multi", chunksize=5_000)
        with engine.connect() as conn:
            loaded = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
        assert loaded == len(df), f"{table}: loaded {loaded} rows, expected {len(df)}"
        print(f"{table:<20} {loaded:>6,} rows")


if __name__ == "__main__":
    main()
