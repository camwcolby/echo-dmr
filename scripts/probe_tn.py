"""Probe Tennessee's Tableau 'Drinking Water Watch' end to end.

    python scripts/probe_tn.py            (no arguments needed; optional PWSID, e.g. TN0000002)

Saves everything in tn_probe/. Send the printed output and the tn_probe folder back.
"""
from __future__ import annotations

import json
import re
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from echo_pull.dw import tn_tableau as TN  # noqa: E402

PWSID = (sys.argv[1] if len(sys.argv) > 1 else "TN0000002").upper()
OUT = Path("tn_probe")
OUT.mkdir(exist_ok=True)


def main():
    print("=== 1. session + bootstrap (Chem/Rad page) ===")
    try:
        ts = TN.scraper_for("ChemRad_page", debug_dir=OUT)
    except Exception as e:  # noqa: BLE001
        print(f"  FAILED: {type(e).__name__}: {str(e)[:400]}")
        traceback.print_exc(limit=3)
        print("  (tn_probe/bootstrap.txt has the raw server reply, if it got that far)")
        return
    wb = ts.getWorkbook()
    print("  worksheets:", [w.name for w in wb.worksheets])
    for w in wb.worksheets:
        try:
            fl = w.getFilters()
        except Exception:  # noqa: BLE001
            fl = []
        print(f"   '{w.name}': {w.data.shape[0]} rows; filters: "
              f"{[(f.get('column'), len(f.get('values', []))) for f in fl]}")

    print(f"\n=== 2. pick {PWSID} ===")
    ws, val, f = TN.system_filter_value(ts, PWSID)
    print(f"  filter sheet: {getattr(ws, 'name', None)}; value: {val}")
    if not val:
        return
    wb2 = ws.setFilter(TN.SYSTEM_FILTER, val)
    for w in wb2.worksheets:
        print(f"   after: '{w.name}': {w.data.shape[0]} rows; columns {list(w.data.columns)[:14]}")
        if len(w.data):
            w.data.to_csv(OUT / f"after_{re.sub(r'[^A-Za-z0-9]+', '_', w.name)[:50]}.csv", index=False)

    print("\n=== 3. widen 'Type' to all sample types (dashboard default is Routine only) ===")
    for w in wb2.worksheets:
        try:
            for fl in w.getFilters():
                if fl.get("column", "").lower() == "type":
                    print(f"   Type values: {fl.get('values')}")
                    wb3 = w.setFilter("Type", fl.get("values"))
                    print("   set Type to all values:",
                          [(x.name, x.data.shape[0]) for x in wb3.worksheets])
                    raise StopIteration
        except StopIteration:
            break
        except Exception as e:  # noqa: BLE001
            print(f"   Type filter on '{w.name}': {type(e).__name__}: {str(e)[:150]}")

    print("\n=== 4. Export -> CSV (crosstab) ===")
    try:
        df, sheet = TN.export_crosstab(ts)
        df.to_csv(OUT / "export_crosstab.csv", index=False)
        print(f"  exported sheet '{sheet}': {len(df)} rows; columns {list(df.columns)}")
        print(df.head(8).to_string())
    except Exception as e:  # noqa: BLE001
        print(f"  export FAILED: {type(e).__name__}: {str(e)[:300]}")
    print("\nDone. Send this output and the tn_probe folder.")


if __name__ == "__main__":
    main()
