"""Federal SDWIS data through EPA Envirofacts (public, no key).

Two URL styles are tried in order, because EPA has been migrating Envirofacts:
  new:  https://data.epa.gov/dmapservice/sdwis.<table>/<column>/equals/<value>/1:N   (JSON)
  old:  https://data.epa.gov/efservice/<TABLE>/<COLUMN>/<value>/JSON
Column names are lower-cased on the way in; every field is optional downstream.
"""
from __future__ import annotations

import re
import time
from urllib.parse import quote

import pandas as pd
import requests

HEADERS = {"User-Agent": "inframark-technical-services-dw-tool/0.1"}
NEW = "https://data.epa.gov/dmapservice"
OLD = "https://data.epa.gov/efservice"
MAX_ROWS = 20000


class FederalError(RuntimeError):
    pass


def _get_json(url: str, timeout: int = 90, retries: int = 2):
    last = None
    for i in range(retries + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            if r.status_code == 200:
                txt = r.text.strip()
                if not txt:
                    return []
                if txt[0] in "[{":
                    return r.json()
                last = FederalError(f"non-JSON response from {url}: {txt[:200]}")
            else:
                last = FederalError(f"HTTP {r.status_code} from {url}: {r.text[:200]}")
        except requests.RequestException as e:
            last = e
        time.sleep(1.5 * (i + 1))
    raise FederalError(str(last))


def query(table: str, filters: list[tuple[str, str, str]]) -> pd.DataFrame:
    """filters: [(column, operator, value)], operator in {'equals', 'contains', 'beginsWith'}."""
    errors = []
    # --- new dmapservice
    path = "/and/".join(f"{c}/{op}/{quote(str(v), safe='')}" for c, op, v in filters)
    try:
        data = _get_json(f"{NEW}/sdwis.{table.lower()}/{path}/1:{MAX_ROWS}")
        if isinstance(data, dict):
            data = data.get("data") or data.get("results") or data.get("Results") or []
        return _frame(data)
    except FederalError as e:
        errors.append(str(e))
    # --- legacy efservice (equals and CONTAINING only)
    oldop = {"equals": "", "contains": "CONTAINING/", "beginsWith": "BEGINNING/"}
    path = "/".join(f"{c.upper()}/{oldop.get(op, '')}{quote(str(v), safe='')}" for c, op, v in filters)
    try:
        return _frame(_get_json(f"{OLD}/{table.upper()}/{path}/ROWS/0:{MAX_ROWS}/JSON"))
    except FederalError as e:
        errors.append(str(e))
    raise FederalError(" | ".join(errors))


def _frame(data) -> pd.DataFrame:
    df = pd.DataFrame(data if isinstance(data, list) else [])
    df.columns = [str(c).strip().lower().split(".")[-1] for c in df.columns]
    return df


def _dates(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce", format="mixed")


# ------------------------------------------------------------------ lookups
PWS_TYPE = {"CWS": "Community", "NTNCWS": "Non-transient non-community",
            "TNCWS": "Transient non-community"}
SOURCE = {"GW": "Groundwater", "SW": "Surface water", "GWP": "Purchased groundwater",
          "SWP": "Purchased surface water", "GU": "Groundwater under direct influence of SW",
          "GUP": "Purchased GWUDI"}
OWNER = {"F": "Federal government", "L": "Local government", "M": "Public/private",
         "N": "Native American", "P": "Private", "S": "State government"}
VIOL_CATEGORY = {"MCL": "Maximum contaminant level", "MRDL": "Max residual disinfectant level",
                 "TT": "Treatment technique", "MR": "Monitoring & reporting",
                 "MON": "Monitoring", "RPT": "Reporting", "Other": "Other"}
STATUS = {"O": "Open", "R": "Returned to compliance", "K": "Known / addressed",
          "A": "Archived"}
# Common SDWIS contaminant / rule codes. Anything else shows as its code.
CONTAMINANT = {
    "1005": "Arsenic", "1025": "Fluoride", "1038": "Nitrate-Nitrite", "1040": "Nitrate",
    "1041": "Nitrite", "2456": "HAA5", "2950": "TTHM", "0999": "Chlorine", "1006": "Chloramine",
    "1008": "Chlorine dioxide", "1009": "Chlorite", "1011": "Bromate", "3100": "Coliform (TCR)",
    "3014": "E. coli", "8000": "Revised Total Coliform Rule", "5000": "Lead and Copper Rule",
    "5200": "Lead and Copper Rule Revisions", "4000": "Gross alpha", "4010": "Combined radium",
    "4006": "Uranium", "7000": "Consumer Confidence Rule", "7500": "Public notice",
    "PB90": "Lead (90th percentile)", "CU90": "Copper (90th percentile)",
}


def contaminant_name(code) -> str:
    c = str(code or "").strip()
    return CONTAMINANT.get(c, CONTAMINANT.get(c.zfill(4), f"Code {c}" if c else "Unspecified"))


# ------------------------------------------------------------------ public functions
def search_systems(name: str = "", state: str = "", pwsid: str = "") -> pd.DataFrame:
    f = []
    if pwsid:
        f.append(("pwsid", "equals", pwsid.upper()))
    if name:
        f.append(("pws_name", "contains", name.upper()))
    if state:
        f.append(("state_code", "equals", state.upper()))
    if not f:
        raise ValueError("Give a PWSID, a name, or a state.")
    df = query("water_system", f)
    if df.empty:
        return df
    keep = ["pwsid", "pws_name", "city_name", "state_code", "pws_type_code",
            "primary_source_code", "population_served_count", "pws_activity_code"]
    out = df[[c for c in keep if c in df.columns]].copy()
    if "population_served_count" in out:
        out["population_served_count"] = pd.to_numeric(out["population_served_count"], errors="coerce")
        out = out.sort_values("population_served_count", ascending=False)
    return out.reset_index(drop=True)


def profile(pwsid: str) -> dict:
    df = query("water_system", [("pwsid", "equals", pwsid.upper())])
    if df.empty:
        return {}
    r = df.iloc[0].to_dict()
    for k in ("population_served_count", "service_connections_count"):
        r[k] = pd.to_numeric(r.get(k), errors="coerce")
    r["pws_type"] = PWS_TYPE.get(str(r.get("pws_type_code")), r.get("pws_type_code"))
    r["source"] = SOURCE.get(str(r.get("primary_source_code")), r.get("primary_source_code"))
    r["active"] = str(r.get("pws_activity_code", "")).upper() == "A"
    return r


def violations(pwsid: str) -> pd.DataFrame:
    df = query("violation", [("pwsid", "equals", pwsid.upper())])
    if df.empty:
        return df
    for c in ("compl_per_begin_date", "compl_per_end_date", "rtc_date",
              "non_compl_per_begin_date", "non_compl_per_end_date"):
        if c in df:
            df[c] = _dates(df[c])
    df["start"] = df.get("compl_per_begin_date", df.get("non_compl_per_begin_date"))
    df["end"] = df.get("compl_per_end_date", df.get("non_compl_per_end_date"))
    cat = df.get("violation_category_code", pd.Series("", index=df.index)).fillna("Other")
    df["category"] = cat.map(lambda c: VIOL_CATEGORY.get(str(c), str(c)))
    df["health_based"] = df.get("is_health_based_ind", pd.Series("", index=df.index)) \
        .astype(str).str.upper().eq("Y")
    df["contaminant"] = df.get("contaminant_code", pd.Series("", index=df.index)).map(contaminant_name)
    df["status"] = df.get("compliance_status_code", pd.Series("", index=df.index)) \
        .map(lambda s: STATUS.get(str(s), str(s) if pd.notna(s) else ""))
    for c in ("viol_measure", "state_mcl"):
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    sort = "start" if "start" in df else df.columns[0]
    return df.sort_values(sort).reset_index(drop=True)


def lead_copper(pwsid: str) -> pd.DataFrame:
    """90th percentile lead/copper by monitoring period (mg/L)."""
    res = query("lcr_sample_result", [("pwsid", "equals", pwsid.upper())])
    if res.empty:
        return res
    try:
        smp = query("lcr_sample", [("pwsid", "equals", pwsid.upper())])
    except FederalError:
        smp = pd.DataFrame()
    if not smp.empty and "sample_id" in res and "sample_id" in smp:
        cols = [c for c in ("sample_id", "sampling_start_date", "sampling_end_date") if c in smp]
        res = res.merge(smp[cols].drop_duplicates("sample_id"), on="sample_id", how="left")
    res["date"] = _dates(res.get("sampling_end_date", res.get("sampling_start_date",
                                                                pd.Series(index=res.index))))
    res["value"] = pd.to_numeric(res.get("sample_measure"), errors="coerce")
    code = res.get("contaminant_code", pd.Series("", index=res.index)).astype(str).str.upper()
    res["analyte"] = code.map({"PB90": "Lead", "CU90": "Copper"}).fillna(code)
    unit = res.get("unit_of_measure", pd.Series("mg/L", index=res.index)).fillna("mg/L").astype(str)
    from .standards import convert
    res["value_mg_l"] = [convert(v, u, "mg/L") if pd.notna(v) else v
                         for v, u in zip(res["value"], unit)]
    return res.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)


def ownership(prof: dict) -> dict:
    """Owner type and the administrative contact EPA holds for a system (SDWIS WATER_SYSTEM).

    SDWIS has no certified-operator or contract-operator field.
    """
    if not prof:
        return {}
    def g(k):
        v = prof.get(k)
        return None if v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() == "" \
            else str(v).strip()
    phone = g("phone_number")
    if phone and re.fullmatch(r"\d{10}", phone):
        phone = f"({phone[:3]}) {phone[3:6]}-{phone[6:]}"
    addr = ", ".join(x for x in (g("address_line1"), g("city_name"),
                                 " ".join(y for y in (g("state_code"), g("zip_code")) if y)) if x)
    return {"owner": OWNER.get(str(g("owner_type_code") or ""), g("owner_type_code")),
            "org": g("org_name"), "contact": g("admin_name"), "phone": phone,
            "email": g("email_addr"), "address": addr or None}
