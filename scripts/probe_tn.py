"""Diagnose automated access to Tennessee's Tableau 'Drinking Water Watch' (Chem/Rad page).

    python scripts/probe_tn.py            (no arguments needed)

Newer Tableau Server (2023+) no longer puts the session config in the page; the browser fetches
it from .../startSession/viewing. This probe does that, then hands the config to tableauscraper
to open the data session, pick one system, and save every table.
Send the printed output plus everything in tn_probe/ back.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests

HOST = "https://data.tn.gov"
SITE, WORKBOOK, VIEW_NAME = "Public", "TNDrinkingWaterWatch", "ChemRad_page"
VIEW = f"{HOST}/t/{SITE}/views/{WORKBOOK}/{VIEW_NAME}"
VIZQL = f"/vizql/t/{SITE}/w/{WORKBOOK}/v/{VIEW_NAME}"
PWSID = (sys.argv[1] if len(sys.argv) > 1 else "TN0000002").upper()
FILTER = "Water System Name & No."
OUT = Path("tn_probe")
OUT.mkdir(exist_ok=True)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
EMBED = {":embed": "y", ":showVizHome": "no"}


def new_session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    return s


def get_config(s):
    print("\n=== Step A: load page, then ask for the session config ===")
    r = s.get(VIEW, params=EMBED, timeout=60)
    print(f"  page: HTTP {r.status_code}; cookies: {sorted(s.cookies.keys())}")
    m = re.search(r'id="tsConfigContainer"[^>]*>(.*?)</textarea>', r.text, re.S)
    if m and m.group(1).strip():
        print("  config was in the page (older Tableau style)")
        return json.loads(m.group(1))
    attempts = [
        ("POST", f"{HOST}{VIZQL}/startSession/viewing", EMBED),
        ("POST", f"{HOST}{VIZQL}/startSession/viewing", {}),
        ("GET", f"{HOST}{VIZQL}/startSession/viewing", EMBED),
    ]
    hdr = {"Accept": "application/json, text/javascript, */*; q=0.01",
           "X-Requested-With": "XMLHttpRequest", "Referer": r.url,
           "Origin": HOST}
    for i, (method, url, params) in enumerate(attempts, 1):
        try:
            resp = s.request(method, url, params=params, headers=hdr, timeout=60)
        except requests.RequestException as e:
            print(f"  try {i} {method} startSession: {type(e).__name__}")
            continue
        (OUT / f"start_{i}.txt").write_text(resp.text, encoding="utf-8")
        print(f"  try {i} {method} startSession params={params}: HTTP {resp.status_code}, "
              f"{len(resp.text):,} chars, starts: {resp.text[:120]!r}")
        try:
            cfg = resp.json()
        except ValueError:
            continue
        if isinstance(cfg, dict) and cfg.get("sessionid"):
            print("  session config OK. keys:", sorted(cfg)[:20])
            print("  vizql_root:", cfg.get("vizql_root"), " sheetId:", cfg.get("sheetId"))
            (OUT / "config.json").write_text(json.dumps(cfg, indent=1), encoding="utf-8")
            return cfg
    return None


def run_scraper(cfg, s):
    print("\n=== Step B: open data session, pick the system, read tables ===")
    try:
        from tableauscraper import TableauScraper
    except ImportError:
        print("  python -m pip install tableauscraper")
        return
    ts = TableauScraper()
    ts.session = s
    fake_page = f'<textarea id="tsConfigContainer">{json.dumps(cfg)}</textarea>'
    real_get = s.get

    def get(url, *a, **k):          # hand tableauscraper the config it expects in the page
        if urlparse(url).path.rstrip("/").endswith(VIEW_NAME):
            resp = requests.Response()
            resp.status_code, resp._content, resp.url = 200, fake_page.encode(), url
            resp.encoding = "utf-8"
            return resp
        return real_get(url, *a, **k)

    s.get = get
    try:
        ts.loads(VIEW)
    except Exception as e:  # noqa: BLE001
        print(f"  bootstrap failed: {type(e).__name__}: {str(e)[:300]}")
        return
    wb = ts.getWorkbook()
    print("  worksheets:", [w.name for w in wb.worksheets])
    for ws in wb.worksheets:
        print(f"   '{ws.name}': {ws.data.shape[0]} rows; columns {list(ws.data.columns)[:12]}")
    for ws in wb.worksheets:
        try:
            filters = ws.getFilters()
        except Exception:  # noqa: BLE001
            continue
        for f in filters:
            if f.get("column") != FILTER:
                continue
            vals = [v for v in f.get("values", []) if str(v).startswith(PWSID)]
            print(f"  filter '{FILTER}' on '{ws.name}': {len(f.get('values', []))} systems; "
                  f"match: {vals[:1]}")
            if not vals:
                return
            wb2 = ws.setFilter(FILTER, vals[0])
            for w2 in wb2.worksheets:
                print(f"   after: '{w2.name}': {w2.data.shape[0]} rows; "
                      f"columns {list(w2.data.columns)[:15]}")
                if len(w2.data):
                    safe = re.sub(r"[^A-Za-z0-9]+", "_", w2.name)[:50]
                    w2.data.to_csv(OUT / f"after_{safe}.csv", index=False)
            print("  saved tn_probe/after_*.csv")
            return
    print("  no 'Water System Name & No.' filter found on any sheet")


if __name__ == "__main__":
    s = new_session()
    cfg = get_config(s)
    if cfg:
        run_scraper(cfg, s)
    else:
        print("\n  No session config. The start_*.txt files show what the server said.")
    print("\nDone. Send this output and everything in tn_probe/.")
