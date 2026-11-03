"""Probe Tennessee's Tableau 'Drinking Water Watch' dashboard and report what data it exposes.

    pip install tableauscraper
    python scripts/probe_tn.py                       # Chem/Rad page, a default system
    python scripts/probe_tn.py "<view URL>" TN0000002

Writes tn_probe/<sheet>.csv for every worksheet found (before and after picking the system) and
prints a summary. Send the printed output and the tn_probe folder back.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    from tableauscraper import TableauScraper
except ImportError:
    sys.exit("Run first:  python -m pip install tableauscraper")

BASE = "https://data.tn.gov/t/Public/views/TNDrinkingWaterWatch/"
CANDIDATE_VIEWS = ["ChemRadSamples", "ChemRad_page", "Chem_Rad_Samples", "ChemRadSamples_page",
                   "WS_page"]
FILTER = "Water System Name & No."
OUT = Path("tn_probe")


def dump(wb, tag):
    OUT.mkdir(exist_ok=True)
    for ws in wb.worksheets:
        df = ws.data
        safe = re.sub(r"[^A-Za-z0-9]+", "_", ws.name)[:60]
        print(f"   [{tag}] sheet '{ws.name}': {df.shape[0]} rows x {df.shape[1]} cols")
        if df.shape[1]:
            print("        columns:", list(df.columns)[:15])
        if len(df):
            df.to_csv(OUT / f"{tag}_{safe}.csv", index=False)


def try_view(url, pwsid):
    print(f"\nLoading {url}")
    ts = TableauScraper()
    ts.loads(url)
    wb = ts.getWorkbook()
    print(f"  worksheets: {[w.name for w in wb.worksheets]}")
    try:
        print("  parameters:", [p.get("column") for p in wb.getParameters()])
    except Exception:  # noqa: BLE001
        pass
    dump(wb, "before")
    for ws in wb.worksheets:
        try:
            filters = ws.getFilters()
        except Exception:  # noqa: BLE001
            continue
        names = [f.get("column") for f in filters]
        if names:
            print(f"  filters on '{ws.name}': {names}")
        for f in filters:
            if f.get("column") == FILTER:
                vals = [v for v in f.get("values", []) if str(v).startswith(pwsid)]
                print(f"  {FILTER}: {len(f.get('values', []))} values; match for {pwsid}: {vals[:1]}")
                if vals:
                    wb2 = ws.setFilter(FILTER, vals[0])
                    dump(wb2, "after")
                    return True
    return False


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else None
    pwsid = (sys.argv[2] if len(sys.argv) > 2 else "TN0000002").upper()
    urls = [url] if url else [BASE + v for v in CANDIDATE_VIEWS]
    for u in urls:
        try:
            if try_view(u.split("?")[0], pwsid):
                print(f"\nDone. Filtered data saved in {OUT}/ (files starting with 'after_').")
                return
        except Exception as e:  # noqa: BLE001
            print(f"  failed: {type(e).__name__}: {str(e)[:200]}")
    print(f"\nNo filtered data came back. Send this output and anything in {OUT}/.")


if __name__ == "__main__":
    main()
