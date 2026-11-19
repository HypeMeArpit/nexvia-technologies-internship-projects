"""
Step 6 - Export the reporting model from MySQL to CSV for Excel Power Pivot.

Why CSV and not a live MySQL connection: Excel's MySQL connector is missing from some Office
editions and needs an extra driver; CSVs work in every Excel, let reviewers refresh the workbook
without a database, and make the Power Automate refresh simpler.

Output: excel/data/*.csv   (dates as YYYY-MM-DD, '.' as decimal separator)
  dimensions : dim_month, dim_account, dim_department, dim_grade, dim_risk_band
  facts      : fact_gl (+ net_debit), fact_budget, fact_budget_driver, fact_kpi_driver
  from views : portfolio_monthly, portfolio_by_risk_band, interest_variance_drivers
  checks     : check_pl_monthly, check_balance_sheet   (SQL results the workbook must match)

Run from the DA-04 folder (after the data is loaded into MySQL):
    python src/export_for_excel.py
"""

from pathlib import Path

import pandas as pd

from db import get_engine

OUT = Path("excel/data")
PERIOD = "month_start BETWEEN '2016-01-01' AND '2018-12-01'"

EXPORTS = {
    "dim_month": f"SELECT * FROM dim_month WHERE {PERIOD} ORDER BY month_start",
    # report_line_order: one sort number per report line (the lowest of its accounts), so
    # Excel can sort report lines; sort_order itself differs between accounts in the same line
    "dim_account": """SELECT a.*, MIN(sort_order) OVER (PARTITION BY report_line) AS report_line_order
                      FROM dim_account a ORDER BY sort_order""",
    "dim_department": "SELECT * FROM dim_department ORDER BY department_code",
    "dim_grade": "SELECT * FROM dim_grade ORDER BY sort_order",
    "dim_risk_band": "SELECT * FROM dim_risk_band ORDER BY sort_order",
    "fact_gl": """SELECT gl_line_id, journal_id, month_start, account_code,
                         COALESCE(department_code, 'ALL') AS department_code,
                         grade, debit, credit, debit - credit AS net_debit, source_system, description
                  FROM fact_gl ORDER BY gl_line_id""",
    "fact_budget": "SELECT * FROM fact_budget ORDER BY month_start, account_code, department_code",
    "fact_budget_driver": "SELECT * FROM fact_budget_driver ORDER BY month_start, grade",
    "fact_kpi_driver": "SELECT * FROM fact_kpi_driver ORDER BY month_start, kpi_code",
    "portfolio_monthly": "SELECT * FROM vw_portfolio_monthly ORDER BY month_start, grade",
    "portfolio_by_risk_band": "SELECT * FROM vw_portfolio_by_risk_band ORDER BY month_start, risk_band_order",
    "interest_variance_drivers": "SELECT * FROM vw_interest_variance_drivers ORDER BY month_start",
    "check_pl_monthly": "SELECT * FROM vw_pl_monthly ORDER BY month_start",
    "check_balance_sheet": "SELECT * FROM vw_balance_sheet ORDER BY month_start",
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    engine = get_engine()
    for name, query in EXPORTS.items():
        df = pd.read_sql(query, engine)
        for col in df.columns:
            if col in ("month_start", "month_end"):
                df[col] = pd.to_datetime(df[col]).dt.strftime("%Y-%m-%d")
            elif df[col].dtype == object:
                # MySQL DECIMAL arrives as Python Decimal: convert so the CSV holds plain numbers
                converted = pd.to_numeric(df[col], errors="coerce")
                if converted.notna().sum() == df[col].notna().sum():
                    df[col] = converted
        num = df.select_dtypes("number").columns
        df[num] = df[num].round(6)
        df.to_csv(OUT / f"{name}.csv", index=False, lineterminator="\n")
        print(f"  {name:<28} {len(df):>6,} rows")
    print(f"Exported {len(EXPORTS)} files to {OUT}/")


if __name__ == "__main__":
    main()
