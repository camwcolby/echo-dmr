"""Key-findings text + Excel workbook for one permit."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from . import analysis as A
from . import qa

MONTHS = dict(enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), start=1))
REPAIRS = ["load_mismatch", "rolling_mismatch", "rolling_filled", "load_mismatch_rough",
           "impossible_value"]


def run(df: pd.DataFrame, design_mgd: float | None = None, recent_months: int = 36) -> dict:
    h = A.headroom(df, recent_months)
    by_month, viol_month = A.seasonality(df, h)
    return {
        "df": df, "headroom": h, "by_month": by_month, "viol_month": viol_month,
        "flow_viol": A.flow_vs_violations(df, design_mgd), "loads": A.loads(df),
        "qa": qa.summary(df), "design_mgd": design_mgd, "recent_months": recent_months,
    }


def _lbl(r):
    return f"{r['parameter']} ({r['stat_base']}, {r['basis']})"


def findings(R: dict) -> list[str]:
    df, h, L = R["df"], R["headroom"], R["loads"]
    out = [f"Data: {df['period_end'].min():%b %Y} to {df['period_end'].max():%b %Y}, "
           f"{df['period_end'].dt.to_period('M').nunique()} months. "
           f"{int(df['is_violation_echo'].sum())} numeric violations on ECHO's record, "
           f"{int(df['is_reporting_violation'].sum())} reporting violations."]
    yrs = R["recent_months"] / 12
    if not h.empty:
        for _, r in h[h["risk_class"] == "chronic"].iterrows():
            if r["headroom_basis"] == "% of limit":
                out.append(f"CHRONIC: {_lbl(r)} median {r['median_recent']:.0%} of limit over the "
                           f"last {yrs:.0f} yrs, at/above 75% in {r['months_over_75_recent']} of "
                           f"{r['n_recent']} months.")
            else:
                out.append(f"CHRONIC: {_lbl(r)} median margin only {r['margin_median_recent']:.2g} "
                           f"{r['headroom_basis'].split('(')[1].rstrip(')')} from the limit.")
        for _, r in h[h["risk_class"] == "watch"].iterrows():
            extra = f" ({r['notes']})" if r["notes"] else ""
            out.append(f"WATCH: {_lbl(r)} median {r['median_recent']:.0%}, p95 "
                       f"{r['p95_recent']:.0%} of limit{extra}.")
        ep = h[h["risk_class"] == "episodic"]
        if len(ep):
            names = sorted(set(ep["parameter"]))
            out.append(f"EPISODIC: {len(ep)} limits with recent excursions or p95 over the limit "
                       f"({', '.join(names)}). Usually wet-weather or upset driven; see flow sheet.")
        cr = h[h["risk_class"] == "check_reporting"]
        for _, r in cr.iterrows():
            out.append(f"CHECK REPORTING: {_lbl(r)}: {r['notes']}")
    fv = R["flow_viol"]
    if not fv.empty and len(fv) > 1:
        lo, hi = fv.iloc[0], fv.iloc[-1]
        out.append(f"FLOW: months with peak-day flow {hi['peak_flow_bin']} had a violation "
                   f"{hi['pct_months_with_violation']:.0%} of the time vs "
                   f"{lo['pct_months_with_violation']:.0%} at {lo['peak_flow_bin']}.")
    for note in L["notes"]:
        out.append(f"LOADS: {note}")
    tr = L["trends"]
    for _, r in tr.iterrows():
        if r["series"] == "q_avg_mgd":
            out.append(f"FLOW TREND: last-5-yr avg {r['mean_last_5yr']:.2f} MGD, "
                       f"{r['trend_pct_per_yr']:+.1f}%/yr; max month {r['max_month_factor']:.2f}x avg.")
        else:
            name = r["series"].replace("_lb_d", "")
            prof = L["profile"][r["series"]]
            out.append(f"{name} LOAD: last-5-yr avg {r['mean_last_5yr']:,.0f} lb/d, "
                       f"{r['trend_pct_per_yr']:+.1f}%/yr; max month {r['max_month_factor']:.2f}x avg; "
                       f"busiest month {MONTHS[int(prof.idxmax())]} (index {prof.max():.0f}) vs "
                       f"lowest {MONTHS[int(prof.idxmin())]} ({prof.min():.0f}), avg = 100.")
    fixed = df[df["qa_flag"].isin(REPAIRS)]
    if len(fixed):
        ex = "; ".join(f"{r['parameter'].split('[')[0].strip()} {r['stat_base']} {r['unit']} "
                       f"{r['period_end']:%b %Y}"
                       for _, r in fixed[fixed["qa_flag"] == "load_mismatch"].iterrows())
        out.append(f"DATA QUALITY: {len(fixed)} values repaired or blanked before analysis"
                   + (f" (gross load typos: {ex})" if ex else "") + ". Details on the QA sheet.")
    rev = df["qa_flag"].isin(["load_conc_disagree", "spike_vs_history"]).sum()
    if rev:
        out.append(f"REVIEW: {rev} values flagged for a human look but left as reported.")
    return out


def write_xlsx(R: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    h, L, df = R["headroom"], R["loads"], R["df"]
    sheets = {
        "Findings": pd.DataFrame({"finding": findings(R)}),
        "Risk ranking": h,
        "Seasonality (pct of limit)": R["by_month"],
        "Violations by month": R["viol_month"],
        "Flow vs violations": R["flow_viol"],
        "Loads annual": L["annual"],
        "Load trends": L["trends"],
        "Monthly profile (idx)": L["profile"],
        "Loads monthly": L["monthly"],
        "QA summary": R["qa"],
        "QA rows": df[df["qa_flag"].notna()][[
            "period_end", "monitoring_location", "parameter", "stat_base", "unit", "value",
            "value_clean", "qa_expected", "limit", "qa_flag"]].sort_values(["qa_flag", "period_end"]),
        "Clean data": df.drop(columns=[c for c in ("is_reported",) if c in df.columns]),
    }
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        for name, t in sheets.items():
            keep_index = name in {"Seasonality (pct of limit)", "Violations by month",
                                  "Loads annual", "Monthly profile (idx)", "Loads monthly"}
            t = t.copy()
            for c in t.columns:
                if pd.api.types.is_datetime64_any_dtype(t[c]):
                    t[c] = t[c].dt.date
            if keep_index and isinstance(t.index, pd.DatetimeIndex):
                t.index = t.index.date
            t.to_excel(xw, sheet_name=name[:31], index=keep_index)
            ws = xw.sheets[name[:31]]
            ws.freeze_panes = "B2" if keep_index else "A2"
            for col in ws.columns:
                width = max(len(str(c.value)) if c.value is not None else 0 for c in col[:200])
                ws.column_dimensions[col[0].column_letter].width = min(max(10, width + 2), 70)
            hdr = [c.value for c in ws[1]]
            for j, name_ in enumerate(hdr, start=1):
                if isinstance(name_, str) and (name_.startswith(("median", "p95", "max_all",
                                                                   "pct_", "at_limit"))
                                               or "pct of limit" in name):
                    for row in ws.iter_rows(min_row=2, min_col=j, max_col=j):
                        for c in row:
                            c.number_format = "0%"
            if name == "Seasonality (pct of limit)":
                for row in ws.iter_rows(min_row=2, min_col=2):
                    for c in row:
                        c.number_format = "0%"
    return path
