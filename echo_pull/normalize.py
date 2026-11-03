"""Turn raw ECHO effluent-chart CSV rows into one clean, analysis-ready table.

Raw ECHO quirks this handles:
  * column names vary in case between endpoints -> lowercased + alias map
  * every number is text, blanks mean "no value", NODI codes explain why
  * limits can be maximums (BOD, TSS) or minimums (DO, pH low, % removal)
  * loads (Q1/Q2, lb/d) and concentrations (C1-C3, mg/L) share one table

Output grain: one row = one permit / outfall / parameter / limit slot / monitoring period.
"""
from __future__ import annotations

import pandas as pd

# canonical name -> list of raw column names ECHO has been seen to use (lowercased)
ALIASES: dict[str, list[str]] = {
    "npdes_id": ["npdes_id", "external_permit_nmbr", "p_id"],
    "outfall": ["perm_feature_nmbr", "outfall"],
    "monitoring_location": ["monitoring_location_desc", "monitoring_location_code"],
    "parameter_code": ["parameter_code"],
    "parameter": ["parameter_desc", "parameter_name"],
    "period_end": ["monitoring_period_end_date"],
    "value_type": ["limit_value_type_code", "value_type_code"],
    "stat_base": ["statistical_base_short_desc", "statistical_base_desc"],
    "stat_base_code": ["statistical_base_code"],
    "limit_qualifier": ["limit_value_qualifier_code"],
    # value/limit/unit are chosen as a matched set in normalize(); see UNIT_SETS
    "limit": ["limit_value_standard_units", "limit_value_nmbr"],
    "limit_unit": ["standard_unit_desc", "limit_unit_desc", "limit_unit_short_desc"],
    "value": ["dmr_value_standard_units", "dmr_value_nmbr"],
    "unit": ["standard_unit_desc", "dmr_unit_desc", "dmr_unit_short_desc"],
    "nodi_code": ["nodi_code"],
    "nodi_desc": ["nodi_desc"],
    "violation_code": ["violation_code"],
    "violation_desc": ["violation_desc"],
    "exceedance_pct": ["exceedence_pct", "exceedance_pct"],
}

# Value and limit must come from the SAME unit family, or we'd compare lb/d to kg/d.
UNIT_SETS = [
    ("dmr_value_standard_units", "limit_value_standard_units", "standard_unit_desc", "standard_unit_desc"),
    ("dmr_value_nmbr", "limit_value_nmbr", "dmr_unit_desc", "limit_unit_desc"),
]

REQUIRED = ["npdes_id", "parameter", "period_end", "value"]

MIN_HINTS = ("MINIMUM", "MIN ", "INST MIN", "% REMOVAL", "PERCENT REMOVAL")
MIN_PARAMS = ("OXYGEN, DISSOLVED", "PERCENT REMOVAL", "% REMOVAL")


def _pick(df: pd.DataFrame, canon: str) -> pd.Series | None:
    for raw in ALIASES[canon]:
        if raw in df.columns:
            return df[raw]
    return None


def _txt(v) -> str:
    return "" if pd.isna(v) else str(v).strip()


def _limit_direction(row) -> str:
    q = _txt(row.get("limit_qualifier"))
    if q.startswith(">"):
        return "min"
    if q.startswith("<") or q == "=":
        return "max"
    sb = _txt(row.get("stat_base")).upper()
    pr = _txt(row.get("parameter")).upper()
    if any(h in sb for h in MIN_HINTS) or any(p in pr for p in MIN_PARAMS):
        return "min"
    return "max"


def _season(month: int) -> str:
    return {12: "winter", 1: "winter", 2: "winter",
            3: "spring", 4: "spring", 5: "spring",
            6: "summer", 7: "summer", 8: "summer"}.get(month, "fall")


def pct_of_limit(value: pd.Series, limit: pd.Series, direction: pd.Series) -> pd.Series:
    """How close to the limit: 1.0 = right at it, >1 = violating, for max AND min limits."""
    value = pd.to_numeric(value, errors="coerce").astype(float)
    limit = pd.to_numeric(limit, errors="coerce").astype(float)
    out = pd.Series(float("nan"), index=value.index)
    ok = value.notna() & limit.notna() & (limit != 0)
    mx = ok & (direction == "max")
    mn = ok & (direction == "min") & (value != 0)
    out[mx] = value[mx] / limit[mx]
    out[mn] = limit[mn] / value[mn]
    return out


def normalize(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return raw
    raw = raw.copy()
    raw.columns = [c.strip().lower() for c in raw.columns]

    out = pd.DataFrame(index=raw.index)
    for canon in ALIASES:
        s = _pick(raw, canon)
        out[canon] = s if s is not None else pd.NA

    for v, l, vu, lu in UNIT_SETS:
        if v in raw.columns:
            out["value"] = raw[v]
            out["limit"] = raw[l] if l in raw.columns else pd.NA
            out["unit"] = raw[vu] if vu in raw.columns else pd.NA
            out["limit_unit"] = raw[lu] if lu in raw.columns else out["unit"]
            out["unit_family"] = "standard" if "standard" in v else "reported"
            break

    missing = [c for c in REQUIRED if out[c].isna().all()]
    if missing:
        raise ValueError(
            f"ECHO CSV is missing expected columns {missing}. "
            f"Columns received: {sorted(raw.columns)[:40]}  -> update ALIASES in normalize.py"
        )

    # text cleanup
    for c in out.columns:
        if not pd.api.types.is_numeric_dtype(out[c]):
            out[c] = out[c].astype("string").str.strip().replace({"": pd.NA})

    out["npdes_id"] = out["npdes_id"].str.upper()
    out["period_end"] = pd.to_datetime(out["period_end"], errors="coerce")
    for c in ("limit", "value", "exceedance_pct"):
        out[c] = pd.to_numeric(out[c], errors="coerce")

    # load vs concentration vs flow, from the unit (value_type code as fallback)
    u = out["unit"].fillna("").str.lower()
    vt = out["value_type"].fillna("").str.upper().str[0]
    out["basis"] = "other"
    out.loc[vt == "C", "basis"] = "conc"
    out.loc[vt == "Q", "basis"] = "load"
    out.loc[u.str.contains(r"mg/l|ug/l|ng/l|#/100|mpn|cfu|s\.u\.|deg"), "basis"] = "conc"
    out.loc[u.str.contains(r"kg/d|lb/d|ton"), "basis"] = "load"
    out.loc[u.str.contains(r"mgd|gpd|gal|m3/d"), "basis"] = "flow"

    out["limit_direction"] = out.apply(_limit_direction, axis=1)

    out["pct_of_limit"] = pct_of_limit(out["value"], out["limit"], out["limit_direction"])

    # Numeric effluent violations only. ECHO also logs late / missing DMRs as violations
    # (D-codes); those are reporting problems, not process problems, so keep them separate.
    vdesc = out["violation_desc"].fillna("").str.lower()
    vcode = out["violation_code"].fillna("").str.upper()
    out["is_reporting_violation"] = vcode.str.startswith("D") | vdesc.str.contains("non-receipt|late")
    out["is_violation"] = (vdesc.str.contains("numeric") | vcode.str.startswith("E")
                           | (out["pct_of_limit"] > 1.0))
    out["is_reported"] = out["value"].notna()
    out["year"] = out["period_end"].dt.year
    out["month"] = out["period_end"].dt.month
    out["season"] = out["month"].map(_season)

    out = out.dropna(subset=["period_end"])
    key = ["npdes_id", "outfall", "monitoring_location", "parameter_code", "parameter",
           "value_type", "stat_base", "unit", "period_end"]
    out = out.drop_duplicates(subset=key, keep="last").reset_index(drop=True)
    return out
