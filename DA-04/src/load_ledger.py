"""
Step 5 - Load the general ledger, budget and driver tables into MySQL.

Input : data/processed/fact_gl.parquet, fact_budget.parquet,
        fact_budget_driver.parquet, fact_kpi_driver.parquet   (from build_ledger.py)
        reports/scorecard/risk_bands.csv                      (from credit_scorecard.py)
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
BANDS_CSV = Path("reports/scorecard/risk_bands.csv")   # from credit_scorecard.py


def main() -> None:
    engine = get_engine()
    with engine.begin() as conn:
        for table in TABLES + ["dim_risk_band"]:
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

    bands = pd.read_csv(BANDS_CSV)
    bands["sort_order"] = bands["risk_band"].str[1].astype(int)   # 'R1 Very low' -> 1
    cols = ["risk_band", "sort_order", "score_from", "score_to", "train_bad_rate", "oot_bad_rate", "avg_pd_36m"]
    bands[cols].to_sql("dim_risk_band", engine, if_exists="append", index=False)
    print(f"{'dim_risk_band':<20} {len(bands):>6,} rows")


if __name__ == "__main__":
    main()
