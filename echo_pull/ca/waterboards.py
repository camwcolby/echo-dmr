"""California Water Boards open data (data.ca.gov, CKAN datastore API; public, no key).

Three datasets, all keyed back to CIWQS:
  * Permits & Orders (CIWQS regulatory measures): every WDR, NPDES, MS4 ... order, with WDID,
    order number, status, dates, design flow, threat/complexity. `facility_id` is the CIWQS
    place ID.
  * eSMR analytical data, one resource per year since 2006: self-monitoring results that
    dischargers submit electronically, keyed by `facility_place_id` (= place ID). Most WDR-only
    dischargers still file PDFs, so many sites have none.
  * SMARTS industrial stormwater: facility info (NOI) and sampling results, keyed by the
    stormwater WDID (e.g. '5S34I012345'). Construction stormwater is deliberately left out.
No dataset carries effluent limits: WDR limits live in the order PDFs.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pandas as pd
import requests

API = "https://data.ca.gov/api/3/action"
HEADERS = {"User-Agent": "inframark-technical-services-ca-tool/0.1"}
ORDERS = "2446e10e-8682-4d7a-952e-07ffe20d4950"
ESMR_PACKAGE = "water-quality-effluent-electronic-self-monitoring-report-esmr-data"
IND_FACILITY = "33e69394-83ec-4872-b644-b9f494de1824"
IND_MONITORING = "7871e8fe-576d-4940-acdf-eca0b399c1aa"
CIWQS_FACILITY = ("https://ciwqs.waterboards.ca.gov/ciwqs/readOnly/CiwqsReportServlet?"
                  "inCommand=drilldown&reportName=facilityAtAGlance&placeID={}")
SMARTS_PUBLIC = "https://smarts.waterboards.ca.gov/smarts/faces/SwSmartsLogin.xhtml"
PAGE = 32000
ESMR_STATIC = Path(__file__).resolve().parents[2] / "data_static" / "esmr_non_npdes.parquet"


class CAError(RuntimeError):
    pass


# ------------------------------------------------------------------ CKAN plumbing
def _call(action: str, params: dict, timeout: int = 90, retries: int = 3) -> dict:
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(f"{API}/{action}", params=params, headers=HEADERS, timeout=timeout)
            body = r.json()
            if body.get("success"):
                return body["result"]
            last = body.get("error")
        except (requests.RequestException, ValueError) as e:
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise CAError(f"data.ca.gov {action} failed: {str(last)[:200]}")


def _search(resource: str, filters: dict | None = None, q: str | None = None,
            fields: list[str] | None = None, max_rows: int = 200_000) -> pd.DataFrame:
    frames, offset = [], 0
    while offset < max_rows:
        p = {"resource_id": resource, "limit": min(PAGE, max_rows - offset), "offset": offset}
        if filters:
            p["filters"] = json.dumps(filters)
        if q:
            p["q"] = q
        if fields:
            p["fields"] = ",".join(fields)
        res = _call("datastore_search", p)
        recs = res.get("records", [])
        frames.append(pd.DataFrame(recs))
        offset += len(recs)
        if len(recs) < p["limit"] or offset >= res.get("total", 0):
            break
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return df.drop(columns=["_id", "rank"], errors="ignore")


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """'NA' / 'NaN' strings -> missing; strip text."""
    for c in df.columns:
        if df[c].dtype == object or str(df[c].dtype).startswith("str"):
            s = df[c].astype("string").str.strip()
            df[c] = s.mask(s.isin(["NA", "NaN", "nan", "None", ""]))
    return df


def ciwqs_url(place_id) -> str:
    return CIWQS_FACILITY.format(str(place_id).strip())


# ------------------------------------------------------------------ what was typed
NPDES_RE = re.compile(r"^CA[A-Z0-9]{7}$")
SW_WDID_RE = re.compile(r"^\d[SRFTV]?\s?\d{2}I\d{6}$")   # industrial stormwater: '1 08I004046', '5S34I012345'
ORDER_RE = re.compile(r"^(R\d-)?\d{2,4}-\d{2,4}(-[A-Z]{2,4})?$")


def kind_of(text: str) -> str:
    t = text.strip().upper()
    if NPDES_RE.match(t):
        return "npdes"
    if SW_WDID_RE.match(t):
        return "sw_wdid"
    if ORDER_RE.match(t):
        return "order"
    if re.fullmatch(r"[0-9][A-Z0-9 ]{6,14}", t) and re.search(r"\d", t[1:]):
        return "wdid"
    return "name"


def _nospace(s: pd.Series) -> pd.Series:
    return s.astype("string").str.replace(r"\s+", "", regex=True).str.upper()


# ------------------------------------------------------------------ permits & orders
ORDER_COLS = ["facility_id", "facility_name", "place_type", "place_address", "place_city",
              "place_county", "facility_region", "program_category", "reg_measure_type",
              "order_number", "wdid", "npdes_number_ca_number", "status", "effective_date",
              "expiration_review_date", "termination_date", "adoption_date", "design_flow",
              "threat_to_water_quality", "complexity", "facility_waste_type", "reclamation",
              "pretreatment", "major_minor", "individual_general", "wdr_review_revise_renew",
              "wdr_review_amend", "wdr_review_pending", "wdr_review_planned", "fee_code",
              "latitude_decimal_degrees", "longitude_decimal_degrees"]


def find_facilities(text: str) -> pd.DataFrame:
    """CIWQS facilities matching a WDID, order number, CA NPDES number or name.
    One row per facility (place ID), active orders first."""
    t, k = text.strip(), kind_of(text)
    if k == "npdes":
        df = _search(ORDERS, filters={"npdes_number_ca_number": t.upper()})
    elif k == "order":
        df = _search(ORDERS, filters={"order_number": t.upper()})
        if df.empty:
            df = _search(ORDERS, q=t, max_rows=500)
    elif k in ("wdid", "sw_wdid"):
        df = _search(ORDERS, q=t, max_rows=500)
        if not df.empty:
            df = df[_nospace(df["wdid"]) == re.sub(r"\s+", "", t.upper())]
    else:
        df = _search(ORDERS, q=t, max_rows=1000)
        if not df.empty:
            words = [w for w in re.findall(r"[A-Za-z0-9]+", t.lower()) if len(w) > 1]
            nm = df["facility_name"].astype("string").str.lower().fillna("")
            df = df[nm.apply(lambda n: all(w in n for w in words))]
    if df.empty:
        return pd.DataFrame()
    df = _clean(df)
    df["active"] = df["status"].eq("Active")
    g = (df.sort_values(["active", "effective_date"], ascending=False)
           .groupby("facility_id", as_index=False)
           .agg(facility_name=("facility_name", "first"), place_type=("place_type", "first"),
                city=("place_city", "first"), county=("place_county", "first"),
                active_orders=("active", "sum"),
                programs=("program_category", lambda s: ", ".join(sorted(set(s.dropna()))))))
    return g.sort_values(["active_orders", "facility_name"], ascending=[False, True]) \
            .reset_index(drop=True)


def facility_orders(place_id) -> pd.DataFrame:
    df = _search(ORDERS, filters={"facility_id": str(place_id)})
    if df.empty:
        return df
    df = _clean(df[[c for c in ORDER_COLS if c in df.columns]].copy())
    for c in ("effective_date", "expiration_review_date", "termination_date", "adoption_date"):
        if c in df:
            df[c] = pd.to_datetime(df[c], errors="coerce")
    df["design_flow"] = pd.to_numeric(df.get("design_flow"), errors="coerce")
    df["active"] = df["status"].eq("Active")
    return df.sort_values(["active", "effective_date"], ascending=False).reset_index(drop=True)


PROGRAM = {"WDR": "Waste discharge requirements", "NPDESWW": "NPDES wastewater",
           "NPDESSW": "NPDES stormwater (MS4)", "LNDISP": "Land disposal", "SSO": "Sanitary sewer",
           "RECYCLING": "Water recycling", "TANKS": "Tanks", "IRRI": "Irrigated lands",
           "ANIMALWASTE": "Animal waste", "CER": "Cleanup", "UNREGS": "Unregulated"}


def _s(v) -> str:
    return "" if v is None or (not isinstance(v, str) and pd.isna(v)) else str(v).strip()


def order_summary(o: pd.Series) -> str:
    bits = [PROGRAM.get(str(o.get("program_category")), _s(o.get("program_category"))),
            f"order {o['order_number']}" if pd.notna(o.get("order_number")) else "",
            f"WDID {o['wdid']}" if pd.notna(o.get("wdid")) else "",
            f"NPDES {o['npdes_number_ca_number']}" if pd.notna(o.get("npdes_number_ca_number")) else ""]
    eff, exp = o.get("effective_date"), o.get("expiration_review_date")
    if pd.notna(eff):
        bits.append(f"effective {eff:%b %Y}")
    if pd.notna(exp):
        word = "expired / review was due" if exp < pd.Timestamp.today() else "expires / review due"
        bits.append(f"{word} {exp:%b %Y}")
    if pd.notna(o.get("design_flow")):
        bits.append(f"design {o['design_flow']:g} MGD")
    if pd.notna(o.get("threat_to_water_quality")) or pd.notna(o.get("complexity")):
        bits.append(f"threat/complexity {_s(o.get('threat_to_water_quality')) or '?'}"
                    f"{_s(o.get('complexity'))}")
    flags = [lab for col, lab in [("wdr_review_revise_renew", "flagged for revise/renew"),
                                  ("wdr_review_amend", "flagged for amendment"),
                                  ("wdr_review_pending", "review pending")]
             if _s(o.get(col)).upper() in ("Y", "YES", "TRUE", "X")]
    return " · ".join(b for b in bits + flags if b)


# ------------------------------------------------------------------ eSMR monitoring
ESMR_FIELDS = ["location", "location_place_type", "parameter", "calculated_method", "qualifier",
               "result", "units", "mdl", "ml", "rl", "sampling_date", "report_name"]
_esmr_ids: dict[int, str] = {}


def esmr_resources() -> dict[int, str]:
    """Year -> resource id, read from the dataset itself so new years appear on their own."""
    if not _esmr_ids:
        pkg = _call("package_show", {"id": ESMR_PACKAGE})
        for r in pkg.get("resources", []):
            m = re.match(r"(\d{4}) eSMR Analytical Data", r.get("name", ""))
            if m and r.get("datastore_active"):
                _esmr_ids[int(m.group(1))] = r["id"]
    return dict(_esmr_ids)


def esmr_history(place_id, path: Path = ESMR_STATIC) -> pd.DataFrame:
    """Pre-built history (scripts/prep_esmr.py) for sites without an active NPDES permit."""
    if not path.exists():
        return pd.DataFrame()
    import pyarrow.parquet as pq
    t = pq.read_table(path, filters=[("facility_place_id", "==", int(place_id))])
    return t.to_pandas()


def esmr_year(place_id, year: int) -> pd.DataFrame:
    rid = esmr_resources().get(year)
    if not rid:
        return pd.DataFrame()
    return _search(rid, filters={"facility_place_id": int(place_id)}, fields=ESMR_FIELDS)


def esmr(place_id, years: int = 10, live_years: int = 1) -> tuple[pd.DataFrame, dict]:
    """eSMR results for one place ID: pre-built history plus the most recent year(s) live.
    The state's API scans a whole year's table per lookup (15-45 s), so only the newest year is
    fetched live; older years come from the prep file when the site is in it."""
    now = pd.Timestamp.today().year
    hist = esmr_history(place_id)
    info = {"history_rows": len(hist), "in_prep_file": not hist.empty}
    frames = [hist] if not hist.empty else []
    built_through = int(pd.to_datetime(hist["sampling_date"]).dt.year.max()) if not hist.empty else None
    live = range(now - live_years + 1, now + 1) if hist.empty or built_through is None \
        else range(max(built_through + 1, now - live_years + 1), now + 1)
    for y in live:
        d = esmr_year(place_id, y)
        if not d.empty:
            frames.append(d)
    info["live_years"] = list(live)
    if not frames:
        return pd.DataFrame(columns=ESMR_FIELDS + ["date"]), info
    d = tidy_esmr(pd.concat(frames, ignore_index=True)[ESMR_FIELDS])
    d = d[d["date"] >= pd.Timestamp(year=now - years, month=1, day=1)]
    return d.reset_index(drop=True), info


def tidy_esmr(d: pd.DataFrame) -> pd.DataFrame:
    d = _clean(d.copy())
    d["date"] = pd.to_datetime(d["sampling_date"], errors="coerce")
    for c in ("result", "mdl", "ml", "rl"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    q = d["qualifier"].fillna("=").str.upper()
    d["nd"] = q.isin(["<", "ND", "<="])
    # a non-detect is drawn at its reporting level (or the reported '<' value when no RL is given)
    d["plot_value"] = d["result"].where(~d["nd"], d["rl"].fillna(d["ml"]).fillna(d["result"]))
    d["calculated"] = d["calculated_method"].notna()
    d["stream"] = d["location_place_type"].fillna("Other").str.replace(" Monitoring", "", regex=False)
    return d.dropna(subset=["date"]).drop_duplicates().reset_index(drop=True)


def esmr_panels(d: pd.DataFrame, max_panels: int = 24) -> pd.DataFrame:
    """One row per parameter+unit, measured (not calculated) results, most data first.
    Flow and the regulars (BOD, TSS, pH, nitrogen ...) float up because they're sampled most."""
    m = d[~d["calculated"] & d["plot_value"].notna()]
    if m.empty:
        return pd.DataFrame()
    t = (m.groupby(["parameter", "units"], dropna=False)
          .agg(results=("plot_value", "size"), streams=("stream", lambda s: ", ".join(sorted(set(s)))),
               first=("date", "min"), last=("date", "max"))
          .reset_index())
    return t.sort_values("results", ascending=False).head(max_panels).reset_index(drop=True)


# ------------------------------------------------------------------ industrial stormwater
IGP_NAL = {
    # Industrial General Permit (Order 2014-0057-DWQ as amended), Table 2. Annual NALs apply to the
    # reporting-year average of every parameter; instantaneous maximum NALs exist only for TSS,
    # O&G and pH. NALs are action levels, not effluent limits: exceeding one is not a violation.
    # Metals marked (H) are hardness dependent; the permit uses EPA's highest value.
    "tss": dict(pat=r"suspended solids|^tss", annual=100, inst=400, unit="mg/L"),
    "og": dict(pat=r"oil (and|&) grease|^o&g", annual=15, inst=25, unit="mg/L"),
    "ph": dict(pat=r"^ph\b", annual=None, inst=None, lo=6.0, hi=9.0, unit="SU"),
    "cod": dict(pat=r"chemical oxygen demand|^cod", annual=120, unit="mg/L"),
    "bod": dict(pat=r"biochemical oxygen demand|^bod", annual=30, unit="mg/L"),
    "zinc": dict(pat=r"^zinc", annual=0.26, unit="mg/L"),
    "copper": dict(pat=r"^copper", annual=0.0332, unit="mg/L"),
    "lead": dict(pat=r"^lead", annual=0.262, unit="mg/L"),
    "cyanide": dict(pat=r"^cyanide", annual=0.022, unit="mg/L"),
    "cadmium": dict(pat=r"^cadmium", annual=0.0053, unit="mg/L"),
    "nickel": dict(pat=r"^nickel", annual=1.02, unit="mg/L"),
    "silver": dict(pat=r"^silver", annual=0.0183, unit="mg/L"),
    "aluminum": dict(pat=r"^alumin", annual=0.75, unit="mg/L"),
    "iron": dict(pat=r"^iron", annual=1.0, unit="mg/L"),
    "nn": dict(pat=r"nitrate.*nitrite|n\+n", annual=0.68, unit="mg/L"),
    "tp": dict(pat=r"^(total )?phosphorus", annual=2.0, unit="mg/L"),
    "ammonia": dict(pat=r"^ammonia", annual=2.14, unit="mg/L"),
    "magnesium": dict(pat=r"^magnesium", annual=0.064, unit="mg/L"),
    "arsenic": dict(pat=r"^arsenic", annual=0.15, unit="mg/L"),
    "mercury": dict(pat=r"^mercury", annual=0.0014, unit="mg/L"),
    "selenium": dict(pat=r"^selenium", annual=0.005, unit="mg/L"),
}
_UNIT_TO_MGL = {"mg/l": 1.0, "ug/l": 1e-3, "µg/l": 1e-3, "ng/l": 1e-6}


def nal_for(parameter: str) -> tuple[str, dict] | tuple[None, None]:
    p = _s(parameter).lower()
    if "dissolved" in p and not p.startswith("ph"):      # NALs are for total metals
        return None, None
    for k, v in IGP_NAL.items():
        if re.search(v["pat"], p):
            return k, v
    return None, None


def industrial_facilities(text: str) -> pd.DataFrame:
    t = text.strip()
    if kind_of(t) == "sw_wdid":
        df = _search(IND_FACILITY, q=t, max_rows=50)
        if not df.empty:
            df = df[_nospace(df["WDID"]) == re.sub(r"\s+", "", t.upper())]
    else:
        df = _search(IND_FACILITY, q=t, max_rows=500)
        if not df.empty:
            words = [w for w in re.findall(r"[A-Za-z0-9]+", t.lower()) if len(w) > 1]
            hay = (df["FACILITY_NAME"].astype("string") + " " + df["OPERATOR_NAME"].astype("string")
                   + " " + df["FACILITY_CITY"].astype("string")).str.lower().fillna("")
            df = df[hay.apply(lambda n: all(w in n for w in words))]
    if df.empty:
        return pd.DataFrame()
    df = _clean(df[df["PERMIT_TYPE"].astype("string").str.contains("Industrial", na=False)].copy())
    df["active"] = df["STATUS"].eq("Active")
    return df.sort_values(["active", "NOI_PROCESSED_DATE"], ascending=False).reset_index(drop=True)


def near_matches(fac: pd.Series, sw: pd.DataFrame) -> pd.DataFrame:
    """Industrial stormwater sites at the same street address as a CIWQS facility."""
    def key(a):
        # street number + first street word, e.g. ('24000', 'civic'); blanks (None/NA) -> None
        m = re.match(r"\s*(\d+)\s+(\S+)", _s(a))
        return (m.group(1), m.group(2).lower()) if m else None
    k = key(fac.get("place_address"))
    if k is None or sw.empty:
        return sw.iloc[0:0]
    # compare row by row in plain Python: pandas can't compare a text column's values to a tuple
    hit = [key(a) == k for a in sw["FACILITY_ADDRESS"]]
    return sw[hit]


def industrial_monitoring(wdid: str) -> pd.DataFrame:
    d = _search(IND_MONITORING, filters={"WDID": wdid}, max_rows=100_000)
    if d.empty:
        return d
    d = _clean(d)
    d["date"] = pd.to_datetime(d["SAMPLE_DATE"], errors="coerce")
    d["result"] = pd.to_numeric(d["RESULT"], errors="coerce")
    d["rl"] = pd.to_numeric(d["RL"], errors="coerce")
    q = d["RESULT_QUALIFIER"].fillna("=").str.upper()
    d["nd"] = q.isin(["<", "ND", "<="])
    d["plot_value"] = d["result"].where(~d["nd"], d["rl"].fillna(d["result"]))
    d["nal_key"] = d["PARAMETER"].map(lambda p: nal_for(p)[0])
    unit = d["UNITS"].astype("string").str.lower().str.strip()
    d["to_mgl"] = unit.map(_UNIT_TO_MGL)
    return d.dropna(subset=["date"]).drop_duplicates().reset_index(drop=True)


def nal_table(m: pd.DataFrame) -> pd.DataFrame:
    """Per parameter with a NAL and per reporting year: average (in mg/L) vs the annual NAL, and
    how many results topped the instantaneous NAL (TSS, O&G) or left the pH range."""
    s = m[m["nal_key"].notna()].copy()
    if s.empty:
        return pd.DataFrame()
    rows = []
    for (key, yr), g in s.groupby(["nal_key", "REPORTING_YEAR"]):
        n = IGP_NAL[key]
        if key == "ph":
            v = g["plot_value"]
            rows.append(dict(nal_key=key, year=yr, results=len(g), average=v.mean(),
                             annual_nal=None, over_annual=None,
                             inst_hits=int(((v < n["lo"]) | (v > n["hi"])).sum())))
            continue
        v = (g["plot_value"] * g["to_mgl"]).dropna()
        if v.empty:
            continue
        avg = v.mean()
        rows.append(dict(nal_key=key, year=yr, results=len(v), average=avg, annual_nal=n["annual"],
                         over_annual=bool(avg > n["annual"]),
                         inst_hits=int((v > n["inst"]).sum()) if n.get("inst") else None))
    return pd.DataFrame(rows)
