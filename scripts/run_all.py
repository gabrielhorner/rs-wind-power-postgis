"""
run_all.py - Run the whole workflow with one command.

  1. scripts/01_carga.py    load the source data into PostGIS
  2. sql/02_analise.sql     spatial analysis (distances, buffers, protected areas)
  3. scripts/03_mapa.py     final map + CSV exports

The PostgreSQL password is asked once and passed to each step through the
PGPASSWORD environment variable (it is never written to disk).

Run from the project folder, with the virtual environment active:
    python scripts\\run_all.py
"""

import os
import sys
import time
import getpass
import subprocess
from pathlib import Path

import psycopg2

PROJECT_DIR = Path(__file__).resolve().parent.parent
SQL_FILE = PROJECT_DIR / "sql" / "02_analise.sql"


def run_python(script):
    print(f"\n=== {script} ===", flush=True)
    result = subprocess.run([sys.executable, str(PROJECT_DIR / "scripts" / script)])
    if result.returncode != 0:
        sys.exit(f"{script} failed - stopping.")


def run_sql(path):
    print(f"\n=== {path.relative_to(PROJECT_DIR)} ===", flush=True)
    start = time.time()
    conn = psycopg2.connect(
        host=os.environ.get("PGHOST", "localhost"),
        port=os.environ.get("PGPORT", "5432"),
        dbname=os.environ.get("PGDATABASE", "eolicas_rs"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ["PGPASSWORD"],
    )
    try:
        with conn, conn.cursor() as cur:
            cur.execute(path.read_text(encoding="utf-8"))
            # Data check from block 0: every plant must have a municipal seat
            cur.execute("""SELECT count(*) FROM wind_plants p
                           LEFT JOIN municipal_seats s ON s.muni_code = p.muni_code
                           WHERE s.muni_code IS NULL""")
            missing = cur.fetchone()[0]
    finally:
        conn.close()
    if missing:
        sys.exit(f"Data check failed: {missing} plants without a municipal seat.")
    print(f"[sql] done in {time.time() - start:.1f} s; data check passed (0 plants without a seat)")


def main():
    if not os.environ.get("PGPASSWORD"):
        os.environ["PGPASSWORD"] = getpass.getpass("Password for PostgreSQL user 'postgres': ")

    run_python("01_carga.py")
    run_sql(SQL_FILE)
    run_python("03_mapa.py")
    print("\nAll steps finished. Results are in the 'output' folder.")


if __name__ == "__main__":
    main()
