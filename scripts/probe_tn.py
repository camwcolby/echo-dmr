"""Diagnose automated access to Tennessee's Tableau 'Drinking Water Watch' (Chem/Rad page).

    python -m pip install tableauscraper
    python scripts/probe_tn.py            (no arguments needed)

Step A: load the page the way a browser does and look for Tableau's session config.
Step B: run tableauscraper with browser headers, pick one system, save every table.
Everything is saved in tn_probe/. Send the printed output plus tn_probe/page.html (and any
after_*.csv files) back.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import requests

VIEW = "https://data.tn.gov/t/Public/views/TNDrinkingWaterWatch/ChemRad_page"
PWSID = (sys.argv[1] if len(sys.argv) > 1 else "TN0000002").upper()
FILTER = "Water System Name & No."
OUT = Path("tn_probe")
OUT.mkdir(exist_ok=True)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
           "Accept-Language": "en-US,en;q=0.9"}


def step_a():
    print("\n=== Step A: page + Tableau session config ===")
    s = requests.Session()
    s.headers.update(HEADERS)
    for params in ({":embed": "y", ":showVizHome": "no"},
                   {":embed": "yes", ":tabs": "no", ":toolbar": "no"}, {}):
        r = s.get(VIEW, params=params, timeout=60)
        (OUT / "page.html").write_text(r.text, encoding="utf-8")
        m = re.search(r'id="tsConfigContainer"[^>]*>(.*?)</textarea>', r.text, re.S)
        print(f"  GET params={params}: HTTP {r.status_code}, {len(r.text):,} chars, "
              f"config found: {bool(m)}, final URL: {r.url[:120]}")
        if m:
            cfg = json.loads(m.group(1).replace("&quot;", '"').replace("&amp;", "&"))
            print("  config keys:", sorted(cfg)[:25])
            print("  vizql_root:", cfg.get("vizql_root"), " sheetId:", cfg.get("sheetId"),
                  " sessionid present:", bool(cfg.get("sessionid")))
            return True
    print("  No config block. Page title:",
          (re.search(r"<title>(.*?)</title>", r.text, re.S) or [None, "?"])[1][:100])
    return False


def step_b():
    print("\n=== Step B: tableauscraper with browser headers ===")
    try:
        from tableauscraper import TableauScraper
    except ImportError:
        print("  tableauscraper not installed: python -m pip install tableauscraper")
        return
    ts = TableauScraper()
    try:
        ts.session.headers.update(HEADERS)
    except Exception:  # noqa: BLE001
        pass
    try:
        ts.loads(VIEW)
    except Exception as e:  # noqa: BLE001
        print(f"  loads failed: {type(e).__name__}: {str(e)[:300]}")
        return
    wb = ts.getWorkbook()
    print("  worksheets:", [w.name for w in wb.worksheets])
    for ws in wb.worksheets:
        print(f"   '{ws.name}': {ws.data.shape[0]} rows, columns {list(ws.data.columns)[:12]}")
        try:
            filters = ws.getFilters()
        except Exception:  # noqa: BLE001
            continue
        for f in filters:
            print(f"     filter '{f.get('column')}' ({len(f.get('values', []))} values)")
            if f.get("column") == FILTER:
                vals = [v for v in f.get("values", []) if str(v).startswith(PWSID)]
                if not vals:
                    print(f"     no value starting with {PWSID}")
                    continue
                wb2 = ws.setFilter(FILTER, vals[0])
                for w2 in wb2.worksheets:
                    print(f"   after filter '{w2.name}': {w2.data.shape[0]} rows, "
                          f"columns {list(w2.data.columns)[:15]}")
                    if len(w2.data):
                        safe = re.sub(r"[^A-Za-z0-9]+", "_", w2.name)[:50]
                        w2.data.to_csv(OUT / f"after_{safe}.csv", index=False)
                print("  Filtered tables saved to tn_probe/after_*.csv")
                return


if __name__ == "__main__":
    step_a()
    step_b()
    print("\nDone. Send this output, tn_probe/page.html, and any tn_probe/after_*.csv files.")
