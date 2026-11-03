"""Tennessee Drinking Water Watch (Tableau Server 2025.3 on data.tn.gov), Chem/Rad samples.

TN retired its classic DWW site for a Tableau dashboard. We talk to it the way the browser does:
  1. GET the view, then POST .../startSession/viewing for the session config (newer Tableau no
     longer embeds it in the page)
  2. POST .../bootstrapSession to open the session
  3. filter 'Water System Name & No.' by value. The dropdown's labels are
     '<PWSID>     <SYSTEM NAME>' (5 spaces), e.g. 'TN0000001     WEST ROBERTSON WATER AUTHORITY'
  4. Export -> CSV of the 'Chem Rad Samples' sheet (the dashboard's own export button)
The date filter defaults to the full record (2008 on) and sample Type to All, so no other filters
are needed. This is Tableau's internal web protocol, not a published API: if TN upgrades or
redesigns the dashboard it can break, and the app says so rather than showing wrong data.
"""
from __future__ import annotations

import io
import json
import re
import time

import pandas as pd
import requests

HOST = "https://data.tn.gov"
SITE, WORKBOOK, VIEW = "Public", "TNDrinkingWaterWatch", "ChemRad_page"
SHEET = "Chem Rad Samples"
SYSTEM_FIELD = "[federated.0fm5m0u1gb5psa0ztpgqj0cz5vfo].[none:Calculation_0138844992323586:nk]"
SYSTEM_CAPTION = "Water System Name & No."
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
EMBED = {":embed": "y", ":showVizHome": "no"}
XHR = {"Accept": "application/json, text/javascript, */*; q=0.01",
       "X-Requested-With": "XMLHttpRequest", "Origin": HOST}


class TNError(RuntimeError):
    pass


class Session:
    """One Tableau viewing session on the TN Chem/Rad dashboard."""

    def __init__(self, debug_dir=None):
        self.http = requests.Session()
        self.http.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
        self.debug = debug_dir
        self.cfg = self._config()
        self.root = HOST + self.cfg["vizql_root"]
        self.sid = self.cfg["sessionid"]
        self.info = self._bootstrap()
        self.system_field = self._find_field(SYSTEM_CAPTION) or SYSTEM_FIELD

    # ---------------------------------------------------------------- setup
    def _save(self, name, text):
        if self.debug is not None:
            (self.debug / name).write_text(text if isinstance(text, str) else json.dumps(text),
                                           encoding="utf-8")

    def _config(self) -> dict:
        page = f"{HOST}/t/{SITE}/views/{WORKBOOK}/{VIEW}"
        r = self.http.get(page, params=EMBED, timeout=60)
        if r.status_code != 200:
            raise TNError(f"TN dashboard page returned HTTP {r.status_code}")
        m = re.search(r'id="tsConfigContainer"[^>]*>(.*?)</textarea>', r.text, re.S)
        if m and m.group(1).strip():
            return json.loads(m.group(1))
        r = self.http.post(f"{HOST}/vizql/t/{SITE}/w/{WORKBOOK}/v/{VIEW}/startSession/viewing",
                           params=EMBED, timeout=60, headers={**XHR, "Referer": r.url})
        try:
            cfg = r.json()
        except ValueError:
            raise TNError(f"TN session start returned HTTP {r.status_code}, not JSON") from None
        if not cfg.get("sessionid"):
            raise TNError("TN session start returned no session id")
        return cfg

    def _bootstrap(self) -> dict:
        c = self.cfg
        dim = json.dumps({"w": 1920, "h": 1080})
        r = self.http.post(
            f"{self.root}/bootstrapSession/sessions/{self.sid}", timeout=120, headers=XHR,
            data={"sheet_id": c.get("sheetId", VIEW), "clientDimension": dim,
                  "worksheetPortSize": dim, "dashboardPortSize": dim,
                  "showParams": c.get("showParams") or json.dumps({"checkpoint": False}),
                  "stickySessionKey": c.get("stickySessionKey") or "",
                  "renderMapsClientSide": "true", "isBrowserRendering": "true",
                  "browserRenderingThreshold": "100", "formatDataValueLocally": "false",
                  "navType": "Nav", "navSrc": "Boot", "devicePixelRatio": "1",
                  "clientRenderPixelLimit": "25000000",
                  "allowAutogenWorksheetPhoneLayouts": "false", "filterTileSize": "200",
                  "locale": "en_US", "language": "en", "verboseMode": "false",
                  ":session_feature_flags": "{}", "keychain_version": "1"})
        self._save("bootstrap.txt", r.text[:2_000_000])
        m = re.match(r"\s*(\d+);", r.text)
        if not m:
            raise TNError(f"TN bootstrap returned HTTP {r.status_code}, unexpected format")
        n = int(m.group(1))
        start = m.end()
        return json.loads(r.text[start:start + n])

    def _find_field(self, caption: str) -> str | None:
        def walk(o):
            if isinstance(o, dict):
                qf = o.get("quickFilter")
                if isinstance(qf, dict) and f"[{caption}]" in (qf.get("columnFullNames") or []):
                    return qf.get("fn")
                for v in o.values():
                    f = walk(v)
                    if f:
                        return f
            elif isinstance(o, list):
                for v in o:
                    f = walk(v)
                    if f:
                        return f
            return None
        return walk(self.info)

    # ---------------------------------------------------------------- commands
    def _command(self, ns_cmd: str, fields: dict) -> dict:
        files = [(k, (None, v if isinstance(v, str) else json.dumps(v))) for k, v in fields.items()]
        r = self.http.post(f"{self.root}/sessions/{self.sid}/commands/{ns_cmd}", files=files,
                           headers=XHR, timeout=180)
        self._save(f"cmd_{ns_cmd.replace('/', '_')}.txt", r.text[:500_000])
        try:
            return r.json()
        except ValueError:
            raise TNError(f"TN command {ns_cmd} returned HTTP {r.status_code}: {r.text[:200]}") from None

    def filter_system(self, label: str) -> dict:
        return self._command("tabdoc/categorical-filter", {
            "visualIdPresModel": {"worksheet": SHEET, "dashboard": VIEW},
            "globalFieldName": self.system_field,
            "membershipTarget": "filter",
            "filterValues": [label],
            "filterUpdateType": "filter-replace"})

    def export_csv(self, sheet: str = SHEET) -> pd.DataFrame:
        dialog = self._command("tabsrv/export-crosstab-server-dialog", {"thumbnailUris": {}})
        sheets = [s for lst in _find_all(dialog, "thumbnailSheetPickerItems")
                  for s in (lst if isinstance(lst, list) else [])]
        pick = next((s for s in sheets if s.get("sheetName") == sheet), None) or next(
            (s for s in sheets if "chem" in str(s.get("sheetName", "")).lower()), None)
        if not pick:
            raise TNError(f"TN export dialog has no '{sheet}' sheet: "
                          f"{[s.get('sheetName') for s in sheets]}")
        res = self._command("tabsrv/export-crosstab-to-csvserver",
                            {"sheetdocId": pick["sheetdocId"], "useTabs": "true",
                             "sendNotifications": "true"})
        key = next(iter(_find_all(res, "resultKey")), None)
        if not key:
            raise TNError("TN export returned no file key")
        r = self.http.get(f"{self.root}/tempfile/sessions/{self.sid}/",
                          params={"key": key, "keepfile": "yes", "attachment": "yes"}, timeout=180)
        raw = r.content
        self._save("export_raw.txt", raw[:200_000].decode("utf-16", errors="replace"))
        text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8", "replace")
        return pd.read_csv(io.StringIO(text), sep="\t", dtype=str)


def _find_all(obj, key):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                yield v
            yield from _find_all(v, key)
    elif isinstance(obj, list):
        for v in obj:
            yield from _find_all(v, key)


def labels_for(pwsid: str, name: str) -> list[str]:
    """Dropdown label candidates: '<PWSID>     <NAME>' is what TN uses; nearby spacings as backup."""
    pwsid, name = pwsid.strip().upper(), re.sub(r"\s+", " ", (name or "").strip().upper())
    out = [f"{pwsid}     {name}"]
    out += [f"{pwsid}{' ' * k}{name}" for k in (4, 6, 3, 2, 1, 7, 8)]
    return out


def chem_rad(pwsid: str, name: str, debug_dir=None) -> tuple[pd.DataFrame, str]:
    """All Chem/Rad sample rows for one TN system, as exported by the dashboard."""
    last = None
    for label in labels_for(pwsid, name):
        s = Session(debug_dir)                       # fresh session per try: filters don't stack
        s.filter_system(label)
        df = s.export_csv()
        if len(df) and df.iloc[:, 0].astype(str).str.contains(pwsid, case=False).any():
            return df, label
        last = (label, len(df))
    raise TNError(f"No TN rows for {pwsid} ({name}); last try {last}")
