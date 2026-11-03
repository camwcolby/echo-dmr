"""Data-quality checks on normalized DMR data.

Runs at read time (not at download time), so improving a check never requires re-pulling from EPA.
Raw `value` is never touched. Adds:
  value_clean          best estimate of the true value (repaired, or blank if untrustworthy)
  pct_of_limit_clean   pct_of_limit recomputed on value_clean
  qa_flag              why a row was touched or deserves a look (blank = clean)
  qa_expected          the value the cross-check expected, where one was computed

Checks, in priority order (a row gets the first flag that fires):
  load_mismatch        monthly-avg load off from conc x flow by >3x (typo-scale) -> replaced with conc x flow
  load_mismatch_rough  other load stat bases off by >3x (only monthly flow to check with) -> blanked
  load_conc_disagree   monthly-avg load off by 35%-3x -> review only, value KEPT. Can be real: when
                       high flow and high conc land on the same days (wet weather), the average of
                       daily loads legitimately exceeds avg conc x avg flow.
  rolling_mismatch     reported rolling-avg load off from the rolling mean of monthly loads by >2x
                       -> replaced with the rebuilt rolling mean
  rolling_filled       rolling-avg load missing but rebuildable from monthly loads -> filled
  impossible_value     % removal over 100, or pH outside 0-14 -> blanked
  spike_vs_history     >10x the series' own 13-month median (review only, value kept; bacteria exempt)
  at_limit             value exactly equal to its limit (review only; often non-detects reported at
                       the limit, which makes headroom look tighter than it is)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .normalize import pct_of_limit

LOAD_FACTOR = {"kg": 3.785, "lb": 8.34}   # mg/L x MGD -> kg/d or lb/d
MONTHLY_TOL = 0.35
ROUGH_X = 3.0
ROLLING_X = 2.0
SPIKE_X = 10.0
BACTERIA = r"coli|coliform|enterococc|fecal"

SERIES = ["npdes_id", "outfall", "monitoring_location", "parameter", "stat_base", "unit"]


def _s(df, col):
    return df[col].astype("string").fillna("")


def is_monthly_avg(stat: pd.Series) -> pd.Series:
    s = stat.astype("string").fillna("").str.upper()
    return s.str.contains(r"MO AVG|MONTHLY AVG|30DA AVG|MO AV$", regex=True) & ~s.str.contains("MN|MIN")


def monthly_flow(df: pd.DataFrame) -> pd.DataFrame:
    """Monthly-average flow (MGD) by location and period."""
    f = df[(df["basis"] == "flow") & df["value"].notna()
           & _s(df, "parameter").str.contains("flow", case=False)
           & _s(df, "unit").str.upper().str.contains("MGD")]
    mo = f[is_monthly_avg(f["stat_base"])]
    return (mo.groupby(["npdes_id", "monitoring_location", "period_end"])["value"]
              .median().rename("flow_mgd").reset_index())


def _attach_flow(rows: pd.DataFrame, flow: pd.DataFrame) -> pd.Series:
    """Flow for each row: same location if reported there, else effluent, else any location."""
    same = rows.merge(flow, on=["npdes_id", "monitoring_location", "period_end"], how="left")["flow_mgd"]
    same.index = rows.index
    eff = flow[_s(flow, "monitoring_location").str.contains("effluent", case=False)]
    eff = eff.groupby(["npdes_id", "period_end"])["flow_mgd"].median()
    anyf = flow.groupby(["npdes_id", "period_end"])["flow_mgd"].median()
    k = pd.MultiIndex.from_frame(rows[["npdes_id", "period_end"]])
    fb1 = pd.Series(eff.reindex(k).values, index=rows.index)
    fb2 = pd.Series(anyf.reindex(k).values, index=rows.index)
    return same.fillna(fb1).fillna(fb2)


def apply(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    df["value_clean"] = df["value"].astype(float)
    df["qa_flag"] = pd.Series(pd.NA, index=df.index, dtype="string")
    df["qa_expected"] = np.nan

    def flag(mask, name, new_value=None, expected=None):
        m = mask & df["qa_flag"].isna()
        df.loc[m, "qa_flag"] = name
        if expected is not None:
            df.loc[m, "qa_expected"] = expected[m]
        if new_value is not None:
            df.loc[m, "value_clean"] = new_value[m] if isinstance(new_value, pd.Series) else new_value

    # ---- 1. load vs concentration x flow
    flow = monthly_flow(df)
    unit = _s(df, "unit").str.lower()
    loads = df[(df["basis"] == "load") & df["value"].notna() & unit.str.match(r"^(kg|lb)")].copy()
    if not loads.empty and not flow.empty:
        key = ["npdes_id", "outfall", "monitoring_location", "parameter", "stat_base", "period_end"]
        conc = df[(df["basis"] == "conc") & df["value"].notna()
                  & _s(df, "unit").str.lower().str.contains("mg/l")]
        conc = conc.drop_duplicates(subset=key)[key + ["value"]].rename(columns={"value": "conc"})
        m = loads.reset_index().merge(conc, on=key, how="inner").set_index("index")
        if not m.empty:
            q = _attach_flow(m, flow)
            fac = _s(m, "unit").str.lower().str[:2].map(LOAD_FACTOR).astype(float)
            expected = (m["conc"] * q * fac).reindex(df.index)
            ratio = df["value"] / expected
            known = ratio.notna() & (expected > 0)
            monthly = is_monthly_avg(df["stat_base"])
            gross = (ratio > ROUGH_X) | (ratio < 1 / ROUGH_X)
            flag(known & monthly & gross, "load_mismatch", new_value=expected, expected=expected)
            flag(known & monthly & ((ratio - 1).abs() > MONTHLY_TOL), "load_conc_disagree",
                 expected=expected)
            flag(known & ~monthly & ((ratio > ROUGH_X) | (ratio < 1 / ROUGH_X)),
                 "load_mismatch_rough", new_value=np.nan, expected=expected)

    # ---- 2. rolling-average loads vs rebuilt rolling mean of (cleaned) monthly loads
    sb = _s(df, "stat_base").str.upper()
    roll_all = (df["basis"] == "load") & sb.str.contains("ROLL") & df["limit"].notna()
    roll = roll_all & df["value"].notna()
    if roll_all.any():
        mo_load = df[(df["basis"] == "load") & is_monthly_avg(df["stat_base"])
                     & df["value_clean"].notna()]
        rebuilt = pd.Series(np.nan, index=df.index)
        for (p, loc, par, u), g in mo_load.groupby(["npdes_id", "monitoring_location",
                                                    "parameter", "unit"]):
            s = g.set_index("period_end")["value_clean"].sort_index()
            s = s[~s.index.duplicated()].asfreq("ME")
            r12 = s.rolling(12, min_periods=10).mean()
            tgt = roll_all & (df["npdes_id"] == p) & (df["monitoring_location"] == loc) \
                & (df["parameter"] == par) & (df["unit"] == u)
            rebuilt[tgt] = r12.reindex(df.loc[tgt, "period_end"]).values
        ratio = df["value"] / rebuilt
        flag(roll & ratio.notna() & ((ratio > ROLLING_X) | (ratio < 1 / ROLLING_X)),
             "rolling_mismatch", new_value=rebuilt, expected=rebuilt)
        flag(roll_all & df["value"].isna() & rebuilt.notna(), "rolling_filled",
             new_value=rebuilt, expected=rebuilt)

    # ---- 2b. physically impossible values -> blanked (e.g. 1000% removal, pH 70)
    par = _s(df, "parameter")
    imp = df["value"].notna() & (
        (par.str.contains("removal", case=False) & (df["value"] > 100))
        | (par.str.match(r"^pH\b") & ((df["value"] > 14) | (df["value"] < 0))))
    flag(imp, "impossible_value", new_value=np.nan)

    # ---- 3. spikes vs the series' own history (review only)
    bact = _s(df, "parameter").str.contains(BACTERIA, case=False)
    cand = df["value_clean"].notna() & ~bact & (df["basis"] != "other")
    med = pd.Series(np.nan, index=df.index)
    for _, g in df[cand].groupby(SERIES, dropna=False):
        g = g.sort_values("period_end")
        med[g.index] = (g["value_clean"].rolling(13, center=True, min_periods=6).median()
                        .values)
    flag(cand & (med > 0) & (df["value_clean"] > SPIKE_X * med), "spike_vs_history",
         expected=med)

    # ---- 4. values sitting exactly at the limit (review only)
    at = df["limit"].notna() & (df["limit"] != 0) & df["value"].notna() & np.isclose(
        df["value"].astype(float), df["limit"].astype(float), rtol=1e-6, atol=0)
    flag(pd.Series(at, index=df.index), "at_limit")

    df["pct_of_limit_clean"] = pct_of_limit(df["value_clean"], df["limit"], df["limit_direction"])

    # Violations: ECHO's own numeric-violation record, plus anything over the limit on CLEANED
    # values (so a data-entry typo can't invent a violation ECHO never recorded).
    vdesc = _s(df, "violation_desc").str.lower()
    vcode = _s(df, "violation_code").str.upper()
    df["is_violation_echo"] = (vdesc.str.contains("numeric") | vcode.str.startswith("E")).astype(bool)
    df["is_violation"] = df["is_violation_echo"] | (df["pct_of_limit_clean"] > 1.0).fillna(False)
    return df


def summary(df: pd.DataFrame) -> pd.DataFrame:
    """One row per flagged series: what fired and how often."""
    f = df[df["qa_flag"].notna()]
    if f.empty:
        return pd.DataFrame(columns=["monitoring_location", "parameter", "stat_base", "unit",
                                     "qa_flag", "n_rows", "first", "last"])
    return (f.groupby(["monitoring_location", "parameter", "stat_base", "unit", "qa_flag"],
                      dropna=False)
             .agg(n_rows=("value", "size"), first=("period_end", "min"),
                  last=("period_end", "max"))
             .reset_index().sort_values(["qa_flag", "n_rows"], ascending=[True, False]))
