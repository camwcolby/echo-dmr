"""Local DuckDB cache. One file, queryable from Python, the DuckDB CLI, or Power BI (via ODBC).

Tables
  facilities  - one row per permit we've looked up
  dmr         - normalized DMR rows (see normalize.py)
  pull_log    - when each permit was last pulled, so we don't hammer EPA
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd

# Set ECHO_DATA_DIR to keep the database outside OneDrive (sync can lock the file mid-write).
DATA_DIR = Path(os.environ.get("ECHO_DATA_DIR") or Path(__file__).resolve().parent.parent / "data")
DEFAULT_DB = DATA_DIR / "echo.duckdb"


def connect(path: Path | str = DEFAULT_DB) -> duckdb.DuckDBPyConnection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute("""
        CREATE TABLE IF NOT EXISTS pull_log (
            npdes_id VARCHAR PRIMARY KEY,
            pulled_at TIMESTAMP,
            years_requested INTEGER,
            rows_loaded INTEGER,
            first_period DATE,
            last_period DATE
        )""")
    con.execute("""
        CREATE TABLE IF NOT EXISTS facilities (
            npdes_id VARCHAR PRIMARY KEY, facility_name VARCHAR, city VARCHAR,
            state VARCHAR, lat DOUBLE, lon DOUBLE, permit_type VARCHAR,
            major_minor VARCHAR, permit_status VARCHAR, last_inspection VARCHAR,
            qtrs_noncompliance_3yr VARCHAR
        )""")
    return con


def save_facilities(con, df: pd.DataFrame) -> None:
    if df.empty:
        return
    cols = [c for c in df.columns if c in
            {r[0] for r in con.execute("DESCRIBE facilities").fetchall()}]
    con.register("_f", df[cols])
    con.execute(f"DELETE FROM facilities WHERE npdes_id IN (SELECT npdes_id FROM _f)")
    con.execute(f"INSERT INTO facilities ({', '.join(cols)}) SELECT {', '.join(cols)} FROM _f")
    con.unregister("_f")


def save_dmr(con, npdes_id: str, df: pd.DataFrame, years: int) -> None:
    """Replace everything for this permit with the fresh pull (simple + avoids stale rows)."""
    npdes_id = npdes_id.upper()
    exists = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name='dmr'").fetchone()[0]
    con.register("_d", df)
    if not exists:
        con.execute("CREATE TABLE dmr AS SELECT * FROM _d")
    else:
        con.execute("DELETE FROM dmr WHERE npdes_id = ?", [npdes_id])
        con.execute("INSERT INTO dmr BY NAME SELECT * FROM _d")
    con.unregister("_d")
    first = df["period_end"].min() if not df.empty else None
    last = df["period_end"].max() if not df.empty else None
    con.execute("DELETE FROM pull_log WHERE npdes_id = ?", [npdes_id])
    con.execute("INSERT INTO pull_log VALUES (?, ?, ?, ?, ?, ?)",
                [npdes_id, datetime.now(), years, len(df), first, last])


def last_pull(con, npdes_id: str):
    return con.execute("SELECT * FROM pull_log WHERE npdes_id = ?",
                       [npdes_id.upper()]).fetchone()


def load_dmr(con, npdes_id: str) -> pd.DataFrame:
    return con.execute("SELECT * FROM dmr WHERE npdes_id = ? ORDER BY period_end",
                       [npdes_id.upper()]).df()
