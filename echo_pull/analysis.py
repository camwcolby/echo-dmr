"""Pursuit analysis on QA'd DMR data (output of qa.apply).

headroom()            every limited parameter, classified chronic / episodic / watch / comfortable
seasonality()         % of limit by calendar month; violations by month
flow_vs_violations()  violation rate vs the month's peak-day flow
loads()               influent flow + loads (lb/d): monthly, annual, trends, monthly profile
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .qa import is_monthly_avg

CHRONIC = 0.75      # recent median at/above this share of the limit = chronic pressure
AT_LIMIT_REVIEW = 0.5   # >= this share of values exactly at the limit -> reporting artifact likely
RISK_ORDER = ["chronic", "episodic", "watch", "check_reporting", "comfortable", "inactive"]
SSO = r"SSO|overflow|bypass"
# Parameters where a ratio to the limit is meaningless; judged by distance to the limit instead.
MARGIN_RULES = [  # (regex on parameter, margin considered tight, label)
    (r"^pH\b", 0.3, "SU"),          # \b so "Phosphorus" does not match
    (r"removal", 3.0, "pct pts"),
    (r"oxygen, dissolved", 1.0, "mg/L"),
]
RISING_PTS = 5      # pts of limit per year that counts as a meaningful upward trend
SERIES = ["outfall", "monitoring_location", "parameter", "stat_base", "basis", "limit_direction"]


def _s(df, col):
    return df[col].astype("string").fillna("")


def _series_key(df: pd.DataFrame) -> pd.Series:
    return df[SERIES].astype("string").fillna("").agg(" | ".join, axis=1)


def _slope_per_year(t: pd.Series, y: pd.Series) -> float:
    ok = y.notna()
    if ok.sum() < 12:
        return np.nan
    x = (t[ok] - t[ok].min()).dt.days / 365.25
    return float(np.polyfit(x, y[ok].astype(float), 1)[0])


# ------------------------------------------------------------------ headroom / risk
def headroom(df: pd.DataFrame, recent_months: int = 36) -> pd.DataFrame:
    lim = df[df["limit"].notna() & df["pct_of_limit_clean"].notna()]
    if lim.empty:
        return pd.DataFrame()
    end = df["period_end"].max()
    cut = end - pd.DateOffset(months=recent_months)
    out = []
    for key, g in lim.groupby(SERIES, dropna=False):
        g = g.sort_values("period_end")
        r = g[g["period_end"] > cut]
        p = g["pct_of_limit_clean"]
        rp = r["pct_of_limit_clean"]
        latest = g.iloc[-1]
        row = dict(zip(SERIES, key))
        rule = next((r for r in MARGIN_RULES
                     if pd.Series([str(row["parameter"])]).str.contains(r[0], case=False).iloc[0]),
                    None)
        row.update({
            "limit_now": latest["limit"], "unit": latest["unit"],
            "first": g["period_end"].min(), "last": g["period_end"].max(), "n": len(g),
            "violations_all": int(g["is_violation"].sum()),
            "violations_recent": int(r["is_violation"].sum()),
            "median_all": p.median(), "p95_all": p.quantile(0.95), "max_all": p.max(),
            "median_recent": rp.median() if len(r) else np.nan,
            "p95_recent": rp.quantile(0.95) if len(r) else np.nan,
            "months_over_75_recent": int((rp >= CHRONIC).sum()), "n_recent": len(r),
            "trend_pts_per_yr": _slope_per_year(
                g["period_end"].iloc[-recent_months:], p.iloc[-recent_months:]) * 100,
            "at_limit_share": float(_s(g, "qa_flag").eq("at_limit").mean()),
        })
        tr = row["trend_pts_per_yr"]
        mr = row["median_recent"]
        row["yrs_to_75"] = ((CHRONIC - mr) * 100 / tr
                            if tr and tr >= RISING_PTS and mr < CHRONIC else np.nan)
        row["headroom_basis"] = "% of limit"
        if rule is not None:
            sign = np.where(g["limit_direction"] == "min", 1, -1)
            margin = sign * (g["value_clean"] - g["limit"])
            rm = margin[g["period_end"] > cut].dropna()
            row["headroom_basis"] = f"margin ({rule[2]})"
            row["margin_median_recent"] = rm.median() if len(rm) else np.nan
            row["margin_p5_recent"] = rm.quantile(0.05) if len(rm) else np.nan
            row["yrs_to_75"] = np.nan

        if row["last"] < end - pd.DateOffset(months=12):
            cls = "inactive"
        elif row["at_limit_share"] >= AT_LIMIT_REVIEW and row["violations_recent"] == 0:
            cls = "check_reporting"
        elif rule is not None:
            if row["violations_recent"] > 0 or row["margin_p5_recent"] < 0:
                cls = "episodic"
            elif row["margin_median_recent"] <= rule[1]:
                cls = "chronic"
            elif row["margin_p5_recent"] <= rule[1]:
                cls = "watch"
            else:
                cls = "comfortable"
        elif mr >= CHRONIC:
            cls = "chronic"
        elif row["violations_recent"] > 0 or row["p95_recent"] >= 1.0:
            cls = "episodic"
        elif row["p95_recent"] >= CHRONIC or (tr >= RISING_PTS and mr >= 0.5):
            cls = "watch"
        else:
            cls = "comfortable"
        row["risk_class"] = cls
        notes = []
        n_at = int(_s(g, "qa_flag").eq("at_limit").sum())
        if row["at_limit_share"] >= 0.2 and n_at >= 5:
            notes.append(f"{row['at_limit_share']:.0%} of values exactly at limit: likely "
                         "non-detects reported at the limit, so headroom is understated")
        if rule is None and tr >= RISING_PTS and cls != "inactive":
            msg = f"rising ~{tr:.0f} pts of limit per year"
            if pd.notna(row["yrs_to_75"]):
                msg += f"; reaches 75% in ~{row['yrs_to_75']:.1f} yrs at this rate"
            notes.append(msg)
        row["notes"] = "; ".join(notes)
        out.append(row)
    h = pd.DataFrame(out)
    h["_o"] = h["risk_class"].map({c: i for i, c in enumerate(RISK_ORDER)})
    h["_m"] = np.where(h["risk_class"] == "chronic", h["median_recent"], h["p95_recent"])
    return h.sort_values(["_o", "_m"], ascending=[True, False]).drop(columns=["_o", "_m"]) \
            .reset_index(drop=True)


# ------------------------------------------------------------------ seasonality
def seasonality(df: pd.DataFrame, h: pd.DataFrame | None = None):
    """(pct-of-limit by month for at-risk series, violations by calendar month)."""
    h = headroom(df) if h is None else h
    risky = h[h["risk_class"].isin(["chronic", "episodic", "watch"])]
    lim = df[df["pct_of_limit_clean"].notna()]
    keyed = lim.assign(_k=_series_key(lim))
    cols = {}
    for k, (_, r) in zip(_series_key(risky), risky.iterrows()):
        g = keyed[keyed["_k"] == k]
        label = f"{r['parameter']} | {r['stat_base']} | {r['basis']}"
        cols[label] = g.groupby("month")["pct_of_limit_clean"].median()
    by_month = pd.DataFrame(cols).reindex(range(1, 13))
    by_month.index.name = "month"

    v = df[df["is_violation"] & ~_s(df, "parameter").str.contains(SSO, case=False)]
    viol = (v.groupby(["month", "parameter"]).size().unstack(fill_value=0)
             .reindex(range(1, 13), fill_value=0))
    viol["total"] = viol.sum(axis=1)
    return by_month, viol


# ------------------------------------------------------------------ flow
def monthly_flows(df: pd.DataFrame) -> pd.DataFrame:
    f = df[(df["basis"] == "flow") & df["value_clean"].notna()
           & _s(df, "parameter").str.contains("flow", case=False)
           & _s(df, "unit").str.upper().str.contains("MGD")]
    if f.empty:
        return pd.DataFrame(columns=["q_avg", "q_peak"])
    loc = _s(f, "monitoring_location")
    eff = f[loc.str.contains("effluent", case=False)]
    inf = f[loc.str.contains("influent", case=False)]
    use = eff if not eff.empty else (inf if not inf.empty else f)
    sb = _s(use, "stat_base").str.upper()
    avg = use[is_monthly_avg(use["stat_base"])].groupby("period_end")["value_clean"].median()
    pk = use[sb.str.contains("MX|MAX")].groupby("period_end")["value_clean"].max()
    return pd.DataFrame({"q_avg": avg, "q_peak": pk}).sort_index()


def flow_vs_violations(df: pd.DataFrame, design_mgd: float | None = None) -> pd.DataFrame:
    q = monthly_flows(df)
    if q["q_peak"].dropna().empty:
        return pd.DataFrame()
    v = df[df["is_violation"] & ~_s(df, "parameter").str.contains(SSO, case=False)]
    nv = v.groupby("period_end").size().reindex(q.index, fill_value=0)
    t = q.assign(violations=nv).dropna(subset=["q_peak"])
    if design_mgd:
        edges = sorted({0, design_mgd, 1.5 * design_mgd, np.inf})
    else:
        edges = sorted(set(np.nanquantile(t["q_peak"], [0, .25, .5, .75, .9]).tolist() + [np.inf]))
        edges = [0] + [round(e, 2) for e in edges[1:-1]] + [np.inf]
    labels = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        labels.append(f"up to {hi:g} MGD" if lo == 0 else
                      f"above {lo:g} MGD" if np.isinf(hi) else f"{lo:g} to {hi:g} MGD")
    t["peak_flow_bin"] = pd.cut(t["q_peak"], edges, labels=labels)
    out = t.groupby("peak_flow_bin", observed=True).agg(
        months=("q_peak", "size"),
        months_with_violation=("violations", lambda s: int((s > 0).sum())),
        violations=("violations", "sum"))
    out["pct_months_with_violation"] = out["months_with_violation"] / out["months"]
    out = out.reset_index()
    out["peak_flow_bin"] = out["peak_flow_bin"].astype(str)
    return out


# ------------------------------------------------------------------ influent loads
LOAD_PARAMS = {
    "BOD": r"BOD",
    "TSS": r"suspended",
    "NH3-N": r"ammonia",
    "TKN": r"kjeldahl",
    "TP": r"phosphorus",
}


def loads(df: pd.DataFrame) -> dict:
    """Influent loads in lb/d from concentration x flow. Returns dict of tables + notes."""
    inf = df[_s(df, "monitoring_location").str.contains("influent", case=False)
             & (df["basis"] == "conc") & _s(df, "unit").str.lower().str.contains("mg/l")
             & is_monthly_avg(df["stat_base"]) & df["value_clean"].notna()
             & ~_s(df, "parameter").str.contains("removal", case=False)]
    q = monthly_flows(df)
    inf_q = df[_s(df, "monitoring_location").str.contains("influent", case=False)
               & (df["basis"] == "flow") & is_monthly_avg(df["stat_base"])
               & df["value_clean"].notna()].groupby("period_end")["value_clean"].median()
    flow = inf_q.combine_first(q["q_avg"]) if not q.empty else inf_q
    monthly = pd.DataFrame({"q_avg_mgd": flow})
    if not q.empty:
        monthly["q_peak_mgd"] = q["q_peak"]
    notes = []
    for name, pat in LOAD_PARAMS.items():
        g = inf[_s(inf, "parameter").str.contains(pat, case=False)
                & ~_s(inf, "parameter").str.contains("percent", case=False)]
        if g.empty:
            continue
        c = g.groupby("period_end")["value_clean"].median()
        monthly[f"{name}_mg_l"] = c
        monthly[f"{name}_lb_d"] = c * flow.reindex(c.index) * 8.34
    monthly = monthly.sort_index()
    load_cols = [c for c in monthly.columns if c.endswith("_lb_d")]
    if not load_cols:
        notes.append("No influent concentrations in the DMRs: loads must come from the RFP, "
                     "plant records, or assumed concentrations.")

    yr = monthly.groupby(monthly.index.year)
    annual = pd.DataFrame({"months": yr["q_avg_mgd"].count(),
                           "q_avg_mgd": yr["q_avg_mgd"].mean(),
                           "q_max_month_mgd": yr["q_avg_mgd"].max()})
    if "q_peak_mgd" in monthly:
        annual["q_peak_day_mgd"] = yr["q_peak_mgd"].max()
    for c in load_cols:
        annual[c.replace("_lb_d", "_avg_lb_d")] = yr[c].mean()
        annual[c.replace("_lb_d", "_max_month_lb_d")] = yr[c].max()
    annual.index.name = "year"

    trend_rows = []
    recent = monthly[monthly.index > monthly.index.max() - pd.DateOffset(months=60)]
    t = pd.Series(recent.index, index=recent.index)
    for c in ["q_avg_mgd"] + load_cols:
        s = recent[c]
        mean = s.mean()
        slope = _slope_per_year(t, s)
        trend_rows.append({
            "series": c, "mean_last_5yr": mean,
            "trend_pct_per_yr": 100 * slope / mean if mean else np.nan,
            "max_month_factor": s.max() / mean if mean else np.nan,
            "p95_month_factor": s.quantile(0.95) / mean if mean else np.nan,
            "n_months": int(s.notna().sum())})
    trends = pd.DataFrame(trend_rows)

    prof = recent[["q_avg_mgd"] + load_cols].groupby(recent.index.month).mean()
    profile = (100 * prof / prof.mean()).round(0)
    profile.index.name = "month"
    return {"monthly": monthly, "annual": annual, "trends": trends,
            "profile": profile, "notes": notes}
