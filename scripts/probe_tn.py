"""Probe Tennessee's Tableau 'Drinking Water Watch' end to end (Chem/Rad samples).

    python scripts/probe_tn.py                    # Adamsville, TN0000002
    python scripts/probe_tn.py TN0000700 "SYSTEM NAME"

Saves everything in tn_probe/. Send the printed output and the tn_probe folder back.
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from echo_pull.dw import tn_tableau as TN  # noqa: E402

PWSID = (sys.argv[1] if len(sys.argv) > 1 else "TN0000002").upper()
NAME = sys.argv[2] if len(sys.argv) > 2 else None
OUT = Path("tn_probe")
OUT.mkdir(exist_ok=True)


def main():
    name = NAME
    if not name:
        try:
            from echo_pull.dw import federal
            name = federal.profile(PWSID).get("pws_name")
        except Exception as e:  # noqa: BLE001
            print(f"  (EPA name lookup failed: {e})")
        name = name or "ADAMSVILLE WATER SYSTEM"
    print(f"System: {PWSID}  name: {name}")

    print("\n=== 1. session + bootstrap ===")
    try:
        s = TN.Session(OUT)
        print(f"  OK. system filter field: {s.system_field}")
    except Exception as e:  # noqa: BLE001
        print(f"  FAILED: {type(e).__name__}: {str(e)[:400]}")
        traceback.print_exc(limit=3)
        return

    print("\n=== 2. filter + export, trying label spacings ===")
    for label in TN.labels_for(PWSID, name)[:4]:
        try:
            s = TN.Session(OUT)
            res = s.filter_system(label)
            print(f"  filter '{label}': response keys {list(res)[:5]}")
            df = s.export_csv()
            hit = len(df) and df.iloc[:, 0].astype(str).str.contains(PWSID, case=False).any()
            print(f"    export: {len(df)} rows; columns {list(df.columns)}; matches {PWSID}: {bool(hit)}")
            if len(df):
                df.to_csv(OUT / "export_crosstab.csv", index=False)
                print(df.head(6).to_string())
            if hit:
                print("\nSUCCESS. Saved tn_probe/export_crosstab.csv")
                return
        except Exception as e:  # noqa: BLE001
            print(f"    FAILED: {type(e).__name__}: {str(e)[:300]}")
    print("\nNo filtered rows yet. Send this output and the tn_probe folder.")


if __name__ == "__main__":
    main()
