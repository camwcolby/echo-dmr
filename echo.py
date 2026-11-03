"""Command line entry point.

  python echo.py search --name "Bridgeport" --state CT
  python echo.py pull CT0100056 --years 10
  python echo.py check CT0100056
  python echo.py analyze TN0020117 --design-mgd 4.5
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from echo_pull import analysis, client, normalize, qa, report, store

RAW_DIR = store.DATA_DIR / "raw"


def cmd_search(a):
    df = client.find_facilities(name=a.name, city=a.city, state=a.state,
                                lat=a.lat, lon=a.lon, radius_mi=a.radius)
    if df.empty:
        print("No facilities found.")
        return
    con = store.connect()
    store.save_facilities(con, df)
    with pd.option_context("display.max_rows", 100, "display.width", 200):
        print(df.to_string(index=False))


def cmd_pull(a):
    con = store.connect()
    npdes = a.npdes_id.upper()
    prev = store.last_pull(con, npdes)
    if prev and not a.force and datetime.now() - prev[1] < timedelta(days=7):
        print(f"{npdes} pulled {prev[1]:%Y-%m-%d}, using cache (use --force to refresh).")
        return
    print(f"Pulling {npdes}, up to {a.years} years...")
    raw = client.fetch_dmr_history(npdes, years=a.years)
    if raw.empty:
        print("ECHO returned no DMR data. Check the permit ID with `search`.")
        return
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = RAW_DIR / f"{npdes}_raw.csv"
    raw.to_csv(raw_path, index=False)  # kept for debugging column mapping
    df = normalize.normalize(raw)
    store.save_dmr(con, npdes, df, a.years)
    print(f"Saved {len(df):,} normalized rows. Raw copy: {raw_path}")
    cmd_check(a)


def cmd_load(a):
    """Normalize a CSV you already downloaded (e.g. from the ECHO website) into the cache."""
    raw = pd.read_csv(a.csv, dtype=str, low_memory=False)
    raw.columns = [c.strip().lower() for c in raw.columns]
    if "npdes_id" not in raw.columns:
        raw["npdes_id"] = a.npdes_id
    df = normalize.normalize(raw)
    con = store.connect()
    store.save_dmr(con, a.npdes_id, df, years=0)
    print(f"Loaded {len(df):,} normalized rows from {a.csv}")
    cmd_check(a)


def _load_clean(npdes_id):
    con = store.connect()
    df = store.load_dmr(con, npdes_id)
    if df.empty:
        sys.exit("Nothing cached for that permit. Run `pull` (or `load`) first.")
    return qa.apply(df)


def cmd_check(a):
    """Sanity printout: coverage, QA, and the risk ranking (anything not comfortable)."""
    df = _load_clean(a.npdes_id)
    print(f"\n=== {a.npdes_id.upper()} ===")
    print(f"Periods: {df['period_end'].min():%Y-%m} to {df['period_end'].max():%Y-%m}"
          f"  ({df['period_end'].dt.to_period('M').nunique()} months with data)")
    print(f"Outfalls: {sorted(df['outfall'].dropna().unique().tolist())}")
    print(f"Monitoring locations: {sorted(df['monitoring_location'].dropna().unique().tolist())}")
    print(f"Rows: {len(df):,}   with limits: {df['limit'].notna().sum():,}   "
          f"unreported (NODI): {df['value'].isna().sum():,}")
    print(f"ECHO numeric violations: {int(df['is_violation_echo'].sum())}   "
          f"reporting violations: {int(df['is_reporting_violation'].sum())}")
    print("QA flags:", df["qa_flag"].value_counts().to_dict() or "none")

    h = analysis.headroom(df)
    show = h[~h["risk_class"].isin(["comfortable", "inactive"])].copy()
    print(f"\nLimits needing attention ({len(show)} of {len(h)}; rest comfortable or inactive):")
    if show.empty:
        return
    show["recent"] = [f"{m:.0%}" if b == "% of limit" else f"{mm:.2g} {b[8:-1]}"
                      for m, mm, b in zip(show["median_recent"], show.get("margin_median_recent",
                                          pd.Series(index=show.index, dtype=float)),
                                          show["headroom_basis"])]
    show["p95"] = show["p95_recent"].map(lambda v: f"{v:.0%}")
    cols = ["risk_class", "parameter", "stat_base", "basis", "recent", "p95",
            "violations_recent", "notes"]
    with pd.option_context("display.max_rows", 60, "display.width", 220,
                           "display.max_colwidth", 70):
        print(show[cols].to_string(index=False))


def cmd_analyze(a):
    df = _load_clean(a.npdes_id)
    R = report.run(df, design_mgd=a.design_mgd, recent_months=a.recent_months)
    print(f"\n=== {a.npdes_id.upper()} key findings ===")
    for line in report.findings(R):
        print(" -", line)
    out = store.DATA_DIR / "reports" / f"{a.npdes_id.upper()}_analysis_{datetime.now():%Y%m%d}.xlsx"
    try:
        report.write_xlsx(R, out)
    except PermissionError:
        sys.exit(f"\nCould not write {out}. Is it open in Excel? Close it and rerun.")
    print(f"\nWorkbook: {out}")


def main(argv=None):
    p = argparse.ArgumentParser(description="EPA ECHO DMR puller")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="find NPDES permits")
    s.add_argument("--name"); s.add_argument("--city"); s.add_argument("--state")
    s.add_argument("--lat", type=float); s.add_argument("--lon", type=float)
    s.add_argument("--radius", type=float, default=5)
    s.set_defaults(fn=cmd_search)

    pl = sub.add_parser("pull", help="download + cache DMR history")
    pl.add_argument("npdes_id"); pl.add_argument("--years", type=int, default=10)
    pl.add_argument("--force", action="store_true")
    pl.set_defaults(fn=cmd_pull)

    ld = sub.add_parser("load", help="normalize a CSV you already have")
    ld.add_argument("npdes_id"); ld.add_argument("csv")
    ld.set_defaults(fn=cmd_load)

    c = sub.add_parser("check", help="sanity summary of cached data")
    c.add_argument("npdes_id")
    c.set_defaults(fn=cmd_check)

    an = sub.add_parser("analyze", help="full analysis -> Excel workbook")
    an.add_argument("npdes_id")
    an.add_argument("--design-mgd", type=float, default=None,
                    help="design/permitted flow; sets the flow-vs-violation bins")
    an.add_argument("--recent-months", type=int, default=36,
                    help="window for 'recent' risk metrics (default 36)")
    an.set_defaults(fn=cmd_analyze)

    a = p.parse_args(argv)
    try:
        a.fn(a)
    except client.EchoError as e:
        sys.exit(f"ECHO error: {e}")


if __name__ == "__main__":
    main()
