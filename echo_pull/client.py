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
