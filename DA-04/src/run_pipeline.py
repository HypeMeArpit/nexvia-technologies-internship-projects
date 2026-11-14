"""
Run the whole DA-04 pipeline with one command, in the right order:

    1. sql/01_create_schema.sql, 02_seed_reference_data.sql   rebuild the database
    2. build_loan_book.py, credit_scorecard.py, build_ledger.py   build the data files
    3. pytest                                                  test them
    4. load_loan_book.py, load_ledger.py                       load them into MySQL
    5. sql/03, 04, 05                                          create reports, run controls

Stops at the first failure and exits with code 1, so a scheduler (Power Automate)
can tell whether the refresh worked. Credentials come from .env.

Run from the DA-04 folder:
    python src/run_pipeline.py                 full run
    python src/run_pipeline.py --skip-build    reuse existing data files (faster)
    python src/run_pipeline.py --skip-tests
    python src/run_pipeline.py --from-raw      also re-extract from the raw Kaggle file (slow)
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import pymysql
from dotenv import load_dotenv
from pymysql.constants import CLIENT

load_dotenv()
SQL = Path("sql")


def step(title):
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}", flush=True)


def run_sql_file(path: Path) -> list[tuple[list[str], list[tuple]]]:
    """Execute every statement in a .sql file; return the result sets that produced rows."""
    conn = pymysql.connect(
        host=os.getenv("MYSQL_HOST", "localhost"), port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "root"), password=os.getenv("MYSQL_PASSWORD", ""),
        client_flag=CLIENT.MULTI_STATEMENTS, autocommit=True)
    results = []
    try:
        with conn.cursor() as cur:
            cur.execute(path.read_text(encoding="utf-8"))
            while True:
                if cur.description:
                    results.append(([d[0] for d in cur.description], list(cur.fetchall())))
                if not cur.nextset():
                    break
    finally:
        conn.close()
    return results


def print_results(results):
    for columns, rows in results:
        widths = [max(len(str(c)), *(len(str(r[i])) for r in rows)) if rows else len(str(c))
                  for i, c in enumerate(columns)]
        print("  " + "  ".join(str(c).ljust(w) for c, w in zip(columns, widths)))
        for r in rows:
            print("  " + "  ".join(str(v).ljust(w) for v, w in zip(r, widths)))
        print()


def check_controls(results, file_name):
    """Control queries return (check_name, issues). Any issues > 0 fails the run."""
    failed = [r for cols, rows in results if cols[:2] == ["check_name", "issues"]
              for r in rows if int(r[1]) != 0]
    if failed:
        raise SystemExit(f"FAILED controls in {file_name}: {failed}")


def main():
    parser = argparse.ArgumentParser(description="Run the DA-04 finance pipeline")
    parser.add_argument("--skip-build", action="store_true", help="reuse existing parquet files")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--from-raw", action="store_true", help="re-run profile_data.py first (slow)")
    args = parser.parse_args()
    start = time.time()

    sys.path.insert(0, str(Path(__file__).parent))  # so we can import the other scripts

    step("1. Rebuild database (schema + reference data)")
    for name in ["01_create_schema.sql", "02_seed_reference_data.sql"]:
        print(f"-> {name}")
        print_results(run_sql_file(SQL / name))

    if not args.skip_build:
        if args.from_raw:
            step("2a. Extract from raw Lending Club file")
            import profile_data
            profile_data.main()
        step("2. Build loan book, credit scorecard and general ledger")
        import build_loan_book, credit_scorecard, build_ledger
        build_loan_book.main()
        credit_scorecard.main()   # adds score, PD and risk band to every loan
        build_ledger.main()       # uses the scorecard PDs for the loan-loss allowance

    if not args.skip_tests:
        step("3. Tests")
        if subprocess.run([sys.executable, "-m", "pytest", "-q"]).returncode != 0:
            raise SystemExit("FAILED: tests did not pass, nothing was loaded")

    step("4. Load into MySQL")
    import load_loan_book, load_ledger
    load_loan_book.main()
    load_ledger.main()

    step("5. Reports and controls")
    for name in ["03_reconcile_loan_book.sql", "04_reporting_views.sql", "05_reconcile_reports.sql"]:
        print(f"-> {name}")
        results = run_sql_file(SQL / name)
        print_results(results)
        check_controls(results, name)

    print(f"\nPIPELINE OK in {time.time() - start:.0f}s")


if __name__ == "__main__":
    main()
