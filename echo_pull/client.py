"""Thin client for EPA ECHO web services (public, no API key).

Two jobs:
  1. find_facilities()  -> look up NPDES permits by name / city / state / lat-long
  2. fetch_dmr_history() -> pull DMR + limits + violations for one permit, chunked by year

Effluent Charts has historically been slow / flaky on long date ranges, so we pull one
calendar year at a time and stitch. Years that come back empty are just skipped, which
is how "10 years, or whatever exists" falls out naturally.
"""
from __future__ import annotations

import io
import time
from datetime import date

import pandas as pd
import requests

BASE = "https://echodata.epa.gov/echo"
CWA_FACILITIES = f"{BASE}/cwa_rest_services.get_facilities"
CWA_QID = f"{BASE}/cwa_rest_services.get_qid"
EFF_DOWNLOAD = f"{BASE}/eff_rest_services.download_effluent_chart"

HEADERS = {"User-Agent": "inframark-technical-services-echo-tool/0.1"}


class EchoError(RuntimeError):
    pass


def _get(url: str, params: dict, retries: int = 3, timeout: int = 90) -> requests.Response:
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
            if r.status_code == 200:
                return r
            last = EchoError(f"HTTP {r.status_code} from {url}: {r.text[:300]}")
        except requests.RequestException as e:  # network blip, timeout
            last = e
        time.sleep(2 * (attempt + 1))
    raise EchoError(f"ECHO request failed after {retries} tries: {last}")


# ---------------------------------------------------------------- facility search
def find_facilities(
    name: str | None = None,
    city: str | None = None,
    state: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    radius_mi: float = 5,
    max_rows: int = 50,
) -> pd.DataFrame:
    """Search CWA/NPDES facilities. Returns one row per permit."""
    params = {"output": "JSON", "responseset": max_rows}
    if name:
        params["p_fn"] = name
    if city:
        params["p_ct"] = city
    if state:
        params["p_st"] = state
    if lat is not None and lon is not None:
        params.update({"p_c1lat": lat, "p_c1lon": lon, "p_radius": radius_mi})
    if len(params) == 2:
        raise ValueError("Give at least a name, city, state, or lat/lon.")

    res = _get(CWA_FACILITIES, params).json().get("Results", {})
    if "Error" in res:
        raise EchoError(res["Error"])
    qid = res.get("QueryID")
    if not qid or int(res.get("QueryRows", 0) or 0) == 0:
        return pd.DataFrame()

    page = _get(CWA_QID, {"output": "JSON", "qid": qid, "pageno": 1}).json()
    facs = page.get("Results", {}).get("Facilities", [])
    df = pd.DataFrame(facs)
    keep = {
        "SourceID": "npdes_id",
        "CWPName": "facility_name",
        "CWPCity": "city",
        "CWPState": "state",
        "FacLat": "lat",
        "FacLong": "lon",
        "CWPPermitTypeDesc": "permit_type",
        "CWPMajorMinorStatusFlag": "major_minor",
        "CWPPermitStatusDesc": "permit_status",
        "CWPDateLastInspection": "last_inspection",
        "CWPQtrsWithNC": "qtrs_noncompliance_3yr",
    }
    return df[[c for c in keep if c in df.columns]].rename(columns=keep)


# ---------------------------------------------------------------- DMR history
def fetch_dmr_year(npdes_id: str, year: int) -> pd.DataFrame:
    params = {
        "p_id": npdes_id,
        "start_date": f"01/01/{year}",
        "end_date": f"12/31/{year}",
    }
    r = _get(EFF_DOWNLOAD, params)
    text = r.text.strip()
    if not text or text.startswith("{") or text.lower().startswith("<"):
        # JSON error payload or HTML error page -> treat as no data for that year
        return pd.DataFrame()
    return pd.read_csv(io.StringIO(text), dtype=str, low_memory=False)


def fetch_dmr_history(npdes_id: str, years: int = 10, verbose: bool = True) -> pd.DataFrame:
    """Pull up to `years` calendar years (current year included). Raw ECHO columns, as strings."""
    npdes_id = npdes_id.strip().upper()
    this_year = date.today().year
    frames = []
    for y in range(this_year - years + 1, this_year + 1):
        df = fetch_dmr_year(npdes_id, y)
        if verbose:
            print(f"  {npdes_id} {y}: {len(df):>6,} rows")
        if not df.empty:
            df["_pull_year"] = y
            frames.append(df)
        time.sleep(0.5)  # be polite to EPA
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------- permit / ownership details
PERMIT_FIELDS = ["CWPName", "CWPCity", "CWPState", "PermitName", "CWPFacilityTypeIndicator",
                 "CWPMajorMinorStatusFlag", "CWPTotalDesignFlowNmbr", "CWPActualAverageFlowNmbr",
                 "CWPPermitStatusDesc", "CWPExpirationDate", "PermittingAgency"]


# ECHO column numbers as of Nov 2026; looked up fresh from ECHO's metadata when possible.
_QCOL_FALLBACK = "1,2,4,5,48,30,62,28,29,53,61,238"
_qcols_cache: list[str] = []


def _qcolumns() -> str:
    if not _qcols_cache:
        try:
            cols = _get(f"{BASE}/cwa_rest_services.metadata", {"output": "JSON"}, retries=1,
                        timeout=30).json()["Results"]["ResultColumns"]
            ids = {c["ObjectName"]: str(c["ColumnID"]) for c in cols}
            _qcols_cache.append(",".join(ids[k] for k in ["SourceID", *PERMIT_FIELDS] if k in ids))
        except Exception:  # noqa: BLE001
            return _QCOL_FALLBACK
    return _qcols_cache[0]


def permit_info(npdes_id: str) -> dict:
    """Who holds the permit and what kind of permit it is, from ECHO's CWA facility search.

    ECHO has no contract-operator or certified-operator field; 'PermitName' is the permittee as
    EPA records it (often the city, sometimes just the plant name again).
    """
    r = _get(CWA_FACILITIES, {"output": "JSON", "p_pid": npdes_id, "responseset": 1},
             retries=1, timeout=30).json().get("Results", {})
    qid = r.get("QueryID")
    if not qid:
        return {}
    facs = _get(CWA_QID, {"output": "JSON", "qid": qid, "pageno": 1, "qcolumns": _qcolumns()},
                retries=1, timeout=30).json().get("Results", {}).get("Facilities", [])
    f = next((x for x in facs if str(x.get("SourceID", "")).upper() == npdes_id.upper()),
             facs[0] if facs else None)
    return {k: f.get(k) for k in PERMIT_FIELDS} if f else {}


def permit_summary(info: dict) -> list[str]:
    """Short phrases for the page header."""
    if not info:
        return []
    out = []
    if info.get("PermitName"):
        out.append(f"Permit holder: {info['PermitName']}")
    kind = info.get("CWPFacilityTypeIndicator")
    mm = {"M": "major", "N": "minor"}.get(str(info.get("CWPMajorMinorStatusFlag") or ""), "")
    if kind or mm:
        out.append(" ".join(x for x in (kind, mm) if x))
    def _f(v):
        v = pd.to_numeric(v, errors="coerce")
        return None if pd.isna(v) else f"{v:g}"
    d, a = _f(info.get("CWPTotalDesignFlowNmbr")), _f(info.get("CWPActualAverageFlowNmbr"))
    if d:
        out.append(f"design {d} MGD" + (f" (actual avg {a})" if a else ""))
    st, exp = info.get("CWPPermitStatusDesc"), pd.to_datetime(info.get("CWPExpirationDate"),
                                                               format="%m/%d/%Y", errors="coerce")
    if st or pd.notna(exp):
        s = f"permit {str(st or '').lower()}".strip()
        if pd.notna(exp):
            s += f", {'expired' if exp < pd.Timestamp.today() else 'expires'} {exp:%b %Y}"
        if info.get("PermittingAgency"):
            s += f" ({info['PermittingAgency']})"
        out.append(s)
    return out
