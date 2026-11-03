"""Tennessee Drinking Water Watch (Tableau Server on data.tn.gov).

TN retired its classic DWW site for a Tableau dashboard. Newer Tableau Server (2023+) no longer
embeds the session config in the page, so we:
  1. GET the view, then POST .../startSession/viewing for the session config (JSON)
  2. hand that config to tableauscraper (patched so it doesn't start a fresh session and lose it)
  3. set the 'Water System Name & No.' filter (and widen the sample Type filter to all values)
  4. export the Chem/Rad table the same way the dashboard's Export -> CSV button does
This is Tableau's internal web protocol, not a published API: if TN upgrades or redesigns the
dashboard it can break, and the app says so instead of showing wrong data.
"""
from __future__ import annotations

import io
import json
import re
import time
from urllib.parse import urlparse

import pandas as pd
import requests

HOST = "https://data.tn.gov"
SITE, WORKBOOK = "Public", "TNDrinkingWaterWatch"
SYSTEM_FILTER = "Water System Name & No."
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
EMBED = {":embed": "y", ":showVizHome": "no"}


class TNError(RuntimeError):
    pass


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    return s


def start_session(view: str, sess=None) -> tuple[requests.Session, dict]:
    sess = sess or _session()
    url = f"{HOST}/t/{SITE}/views/{WORKBOOK}/{view}"
    r = sess.get(url, params=EMBED, timeout=60)
    if r.status_code != 200:
        raise TNError(f"TN dashboard page returned HTTP {r.status_code}")
    m = re.search(r'id="tsConfigContainer"[^>]*>(.*?)</textarea>', r.text, re.S)
    if m and m.group(1).strip():
        return sess, json.loads(m.group(1))
    r = sess.post(f"{HOST}/vizql/t/{SITE}/w/{WORKBOOK}/v/{view}/startSession/viewing",
                  params=EMBED, timeout=60,
                  headers={"Accept": "application/json, text/javascript, */*; q=0.01",
                           "X-Requested-With": "XMLHttpRequest", "Origin": HOST, "Referer": r.url})
    try:
        cfg = r.json()
    except ValueError:
        raise TNError(f"TN startSession returned HTTP {r.status_code}, not JSON") from None
    if not cfg.get("sessionid"):
        raise TNError("TN startSession returned no session id")
    return sess, cfg


def scraper_for(view: str, debug_dir=None):
    """A tableauscraper TableauScraper bootstrapped on our own session."""
    from tableauscraper import TableauScraper
    from tableauscraper import api as tsapi

    sess, cfg = start_session(view)
    page = f'<textarea id="tsConfigContainer">{json.dumps(cfg)}</textarea>'
    ts = TableauScraper()
    orig_set, orig_viz, orig_data = tsapi.setSession, tsapi.getTableauViz, tsapi.getTableauData

    def set_session(scraper):
        scraper.session = sess

    def get_viz(scraper, session, url, params={}):
        return page

    def get_data(scraper):
        c = scraper.tableauData
        r = scraper.session.post(
            f'{scraper.host}{c["vizql_root"]}/bootstrapSession/sessions/{c["sessionid"]}',
            data={"sheet_id": c["sheetId"],
                  "clientDimension": json.dumps({"w": 1920, "h": 1080}),
                  "worksheetPortSize": json.dumps({"w": 1920, "h": 1080}),
                  "dashboardPortSize": json.dumps({"w": 1920, "h": 1080}),
                  "showParams": c.get("showParams") or json.dumps({"checkpoint": False}),
                  "stickySessionKey": c.get("stickySessionKey") or "",
                  "renderMapsClientSide": "true", "isBrowserRendering": "true",
                  "browserRenderingThreshold": "100", "formatDataValueLocally": "false",
                  "navType": "Nav", "navSrc": "Boot", "devicePixelRatio": "1",
                  "clientRenderPixelLimit": "25000000",
                  "allowAutogenWorksheetPhoneLayouts": "false",
                  "filterTileSize": "200", "locale": "en_US", "language": "en",
                  "verboseMode": "false", ":session_feature_flags": "{}",
                  "keychain_version": "1"},
            headers={"X-Requested-With": "XMLHttpRequest", "Origin": HOST},
            verify=scraper.verify)
        scraper.lastActionTime = time.time()
        if debug_dir is not None:
            (debug_dir / "bootstrap.txt").write_text(r.text[:2_000_000], encoding="utf-8")
        return r.text

    tsapi.setSession, tsapi.getTableauViz, tsapi.getTableauData = set_session, get_viz, get_data
    try:
        ts.loads(f"{HOST}/t/{SITE}/views/{WORKBOOK}/{view}")
    finally:
        tsapi.setSession, tsapi.getTableauViz, tsapi.getTableauData = orig_set, orig_viz, orig_data
    return ts


def _find_all(obj, key):
    """Every value stored under `key` anywhere in a nested JSON structure."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                yield v
            yield from _find_all(v, key)
    elif isinstance(obj, list):
        for v in obj:
            yield from _find_all(v, key)


def export_crosstab(ts, want: str = "chem") -> tuple[pd.DataFrame, str]:
    """The dashboard's Export -> CSV: list exportable sheets, export the one matching `want`."""
    from tableauscraper import api as tsapi
    dialog = tsapi.exportCrosstabServerDialog(ts)
    items = [i for i in _find_all(dialog, "thumbnailSheetPickerItems")]
    sheets = [s for lst in items for s in (lst if isinstance(lst, list) else [])]
    if not sheets:
        raise TNError("TN export dialog listed no sheets")
    pick = next((s for s in sheets if want in str(s.get("sheetName", "")).lower()), sheets[0])
    res = tsapi.exportCrosstabToCsvServer(ts, pick["sheetdocId"])
    key = next(iter(_find_all(res, "resultKey")), None)
    if not key:
        raise TNError("TN export returned no file key")
    text = tsapi.downloadCrossTabData(ts, key)
    df = pd.read_csv(io.StringIO(text), sep="\t", dtype=str)
    return df, str(pick.get("sheetName"))


def system_filter_value(ts, pwsid: str):
    wb = ts.getWorkbook()
    for ws in wb.worksheets:
        try:
            for f in ws.getFilters():
                if f.get("column") == SYSTEM_FILTER:
                    vals = [v for v in f.get("values", []) if str(v).upper().startswith(pwsid.upper())]
                    return ws, (vals[0] if vals else None), f
        except Exception:  # noqa: BLE001
            continue
    return None, None, None
