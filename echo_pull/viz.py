"""Charts for the Streamlit page (Plotly).

Design rules (Tufte / house dataviz method):
  * the data is the ink: thin marks, hairline grid, no chart junk
  * one measure per panel, never dual axes; panels share the time axis so the eye can compare
  * the limit is drawn as the limit IN FORCE each month (a step line), so seasonal limits and
    permit-renewal changes appear exactly where they applied
  * color has fixed jobs: blue = reported value, ink = permit limit, critical red = violation
    (always with a text label in the hover and legend, never color alone)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from . import analysis as A

BLUE = "#2a78d6"          # categorical slot 1: reported values
LIMIT_INK = "#52514e"     # secondary ink: permit limit (a reference, not a series)
CRITICAL = "#d03b3b"      # status critical: violations only
MUTED = "#898781"
GRID = "rgba(137,135,129,0.25)"   # hairline that reads on light and dark surfaces
FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'
MON = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()

PANEL_KEY = ["outfall", "monitoring_location", "parameter", "stat_base", "unit"]


def _base_layout(fig: go.Figure, height: int, title: str | None = None) -> go.Figure:
    fig.update_layout(
        height=height, margin=dict(l=8, r=12, t=30 if title else 8, b=8),
        title=dict(text=title, x=0, xanchor="left", font=dict(size=13)) if title else None,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT, size=11), showlegend=False, hovermode="closest",
        hoverlabel=dict(font=dict(family=FONT, size=12)),
    )
    fig.update_xaxes(showgrid=False, linecolor=GRID, ticks="outside", tickcolor=GRID,
                     ticklen=3, zeroline=False)
    fig.update_yaxes(gridcolor=GRID, gridwidth=1, zeroline=False, linecolor=GRID,
                     ticks="", automargin=True)
    return fig


def _fmt(v, unit=""):
    if v is None or (isinstance(v, float) and np.isnan(v)) or pd.isna(v):
        return "n/a"
    s = f"{v:,.0f}" if abs(v) >= 1000 else f"{v:.3g}"
    return f"{s} {unit}".strip()


# ============================================================== flow & loads
LOAD_PANELS = [
    ("q_avg_mgd", "Flow", "MGD"),
    ("BOD_lb_d", "Influent BOD load", "lb/d"),
    ("TSS_lb_d", "Influent TSS load", "lb/d"),
]


def annual_table(L: dict, window_start: pd.Timestamp | None = None) -> pd.DataFrame:
    m = L["monthly"]
    if window_start is not None:
        m = m[m.index > window_start]
    rows = {}
    for col, label, unit in LOAD_PANELS:
        if col in m:
            g = m[col].groupby(m.index.year)
            rows[f"{label} ({unit}), annual avg"] = g.mean()
            rows[f"{label} months reported"] = g.count()
    t = pd.DataFrame(rows)
    t.index.name = "Year"
    return t


def annual_panel(L: dict, col: str, label: str, unit: str, x_range, window_start=None):
    """Monthly values as faint dots; annual average as the bold line. Partial years hollow."""
    m = L["monthly"]
    if window_start is not None:
        m = m[m.index > window_start]
    if col not in m or m[col].dropna().empty:
        return None
    s = m[col].dropna()
    g = s.groupby(s.index.year)
    ann = pd.DataFrame({"avg": g.mean(), "n": g.count()})
    ann["x"] = pd.Series(s.index, index=s.index).groupby(s.index.year).apply(
        lambda d: d.min() + (d.max() - d.min()) / 2)    # centre of the months actually reported
    ann["partial"] = ann["n"] < 12

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=s.index, y=s.values, mode="markers",
        marker=dict(size=5, color=BLUE, opacity=0.28, line=dict(width=0)),
        hovertemplate="%{x|%b %Y}: %{y:,.3~r} " + unit + "<extra>monthly</extra>"))
    fig.add_trace(go.Scatter(
        x=ann["x"], y=ann["avg"], mode="lines+markers",
        line=dict(color=BLUE, width=2),
        marker=dict(size=8, color=np.where(ann["partial"], "rgba(0,0,0,0)", BLUE).tolist(),
                    line=dict(color=BLUE, width=2)),
        customdata=np.c_[ann.index, ann["n"]],
        hovertemplate="%{customdata[0]} annual avg: %{y:,.3~r} " + unit +
                      "<br>%{customdata[1]} months reported<extra></extra>"))
    # selective direct labels: first and last annual value only
    full = ann[~ann["partial"]]
    for _, r in (full.iloc[[0, -1]] if len(full) > 1 else full).iterrows():
        fig.add_annotation(x=r["x"], y=r["avg"], text=_fmt(r["avg"]), showarrow=False,
                           yshift=14, font=dict(size=11))
    _base_layout(fig, 260, f"{label} ({unit})")
    fig.update_xaxes(range=x_range)
    fig.update_yaxes(rangemode="tozero")
    return fig


# ============================================================== permit limits
def panel_list(df: pd.DataFrame, h: pd.DataFrame) -> pd.DataFrame:
    """One row per limited series, with its risk info, sorted riskiest first."""
    lim = df[df["limit"].notna()]
    if lim.empty:
        return pd.DataFrame()
    p = (lim.groupby(PANEL_KEY + ["basis", "limit_direction"], dropna=False)
            .agg(n_lim=("limit", "size")).reset_index())
    want = ["risk_class", "median_recent", "p95_recent", "violations_recent", "violations_all",
            "headroom_basis", "margin_median_recent", "notes"]
    if not h.empty:
        p = p.merge(h[A.SERIES + [c for c in want if c in h.columns]], on=A.SERIES, how="left")
    for c in want:
        if c not in p:
            p[c] = np.nan
    p["risk_class"] = p["risk_class"].fillna("inactive")
    order = {c: i for i, c in enumerate(A.RISK_ORDER)}
    p["_o"] = p["risk_class"].map(order).fillna(len(order))
    p["_m"] = np.where(p["risk_class"] == "chronic", p["median_recent"], p["p95_recent"])
    p = p.sort_values(["_o", "_m"], ascending=[True, False]).drop(columns=["_o", "_m"])
    return p.reset_index(drop=True)


def _select(df, row) -> pd.DataFrame:
    m = np.ones(len(df), dtype=bool)
    for c in PANEL_KEY:
        v = row[c]
        col = df[c]
        m &= (col.isna() if pd.isna(v) else (col == v).fillna(False)).to_numpy()
    return df[m].sort_values("period_end")


def _month_runs(month_to_limit: dict) -> list[tuple[list[int], float]]:
    """Group calendar months with equal limits into circular runs, e.g. Nov-Apr."""
    months = sorted(month_to_limit)
    if len(months) < 12:
        return []
    vals = [month_to_limit[m] for m in range(1, 13)]
    # rotate so a run boundary starts the list
    start = next((i for i in range(12) if vals[i] != vals[i - 1]), 0)
    runs, cur = [], [(start % 12) + 1]
    for k in range(1, 12):
        i = (start + k) % 12
        if vals[i] == vals[i - 1]:
            cur.append(i + 1)
        else:
            runs.append((cur, vals[i - 1]))
            cur = [i + 1]
    runs.append((cur, vals[(start + 11) % 12]))
    return runs


def limit_story(g: pd.DataFrame, unit: str, data_start=None, data_end=None) -> list[str]:
    """Plain-language description of seasonal limits and limit changes for one series."""
    out = []
    lim = g.dropna(subset=["limit"])
    if lim.empty:
        return out
    last = lim["period_end"].max()
    recent = lim[lim["period_end"] > last - pd.DateOffset(months=24)]
    by_m = recent.groupby("month")["limit"].agg(lambda s: s.mode().iloc[0]).to_dict()
    runs = _month_runs(by_m) if len(set(by_m.values())) > 1 else []
    runs.sort(key=lambda r: (r[0][0] - 10) % 12)        # read from late fall: Nov-Apr first
    if runs:
        parts = [f"{_fmt(v, unit)} {MON[ms[0]-1]}–{MON[ms[-1]-1]}" if len(ms) > 1
                 else f"{_fmt(v, unit)} {MON[ms[0]-1]}" for ms, v in runs]
        out.append("Seasonal limit: " + "; ".join(parts))

    # changes not explained by season: compare each month with the same month a year earlier
    s = lim.set_index("period_end")["limit"]
    s = s[~s.index.duplicated()]
    changes = []
    for t, v in s.items():
        prev = s.get(t - pd.offsets.MonthEnd(12))
        if prev is not None and not pd.isna(prev) and not np.isclose(prev, v):
            changes.append((t, prev, v))
    first_lim = s.index.min()
    data_start = g["period_end"].min() if data_start is None else data_start
    data_end = g["period_end"].max() if data_end is None else data_end
    if first_lim > data_start + pd.DateOffset(months=6):
        out.append(f"Limit first applies {first_lim:%b %Y}")
    seen = set()
    for t, prev, v in changes:
        k = (round(float(prev), 6), round(float(v), 6))
        if k in seen:
            continue
        seen.add(k)
        out.append(f"Limit changed {t:%b %Y}: {_fmt(prev)} → {_fmt(v, unit)}")
        if len(seen) >= 2:
            break
    if lim["period_end"].max() < data_end - pd.DateOffset(months=6):
        out.append(f"No limit reported after {lim['period_end'].max():%b %Y}")
    return out


def _gapped(x: list, y: list, max_gap_days: int) -> tuple[list, list]:
    """Insert None where consecutive points are further apart than max_gap_days."""
    X, Y = [], []
    for i, (a, b) in enumerate(zip(x, y)):
        if i and (a - x[i - 1]).days > max_gap_days:
            X.append(None)
            Y.append(None)
        X.append(a)
        Y.append(b)
    return X, Y


def limit_panel(df: pd.DataFrame, row, x_range, mode: str = "value") -> go.Figure | None:
    """mode 'value': reported value vs the limit in force. mode 'pct': % of the limit in force."""
    g = _select(df, row)
    if g.empty:
        return None
    unit = str(row["unit"]) if pd.notna(row["unit"]) else ""
    start = (g["period_end"] - pd.offsets.MonthBegin(1)).dt.normalize()
    mid = start + pd.Timedelta(days=14)
    is_min = row["limit_direction"] == "min"

    margin_param = any(pd.Series([str(row["parameter"])]).str.contains(rx, case=False).iloc[0]
                       for rx, _, _ in A.MARGIN_RULES)
    forced_value = mode == "pct" and margin_param
    if forced_value:
        mode = "value"      # a ratio to a pH / DO / % removal floor is meaningless
    if mode == "pct":
        y = g["pct_of_limit_clean"] * 100
        yunit = "% of limit"
    else:
        y = g["value_clean"]
        yunit = unit

    fig = go.Figure()

    # ---- limit in force, as a step spanning each monitoring month
    if mode == "pct":
        if g["limit"].notna().any():
            fig.add_hline(y=100, line=dict(color=LIMIT_INK, width=1.5))
    else:
        lx, ly = [], []
        prev_end = None
        for s0, e0, v in zip(start, g["period_end"], g["limit"]):
            if pd.isna(v):
                if lx and lx[-1] is not None:
                    lx.append(None); ly.append(None)
                prev_end = None
                continue
            if prev_end is not None and (s0 - prev_end).days > 3:
                lx.append(None); ly.append(None)
            lx += [s0, e0 + pd.Timedelta(days=1)]
            ly += [v, v]
            prev_end = e0 + pd.Timedelta(days=1)
        fig.add_trace(go.Scatter(
            x=lx, y=ly, mode="lines", line=dict(color=LIMIT_INK, width=1.5, shape="linear"),
            name="Permit limit", hoverinfo="skip", connectgaps=False))

    # ---- reported values
    ok = y.notna()
    X, Y = _gapped(list(mid[ok]), list(y[ok]), 75)
    fig.add_trace(go.Scatter(x=X, y=Y, mode="lines", line=dict(color=BLUE, width=1),
                             opacity=0.45, hoverinfo="skip", connectgaps=False))

    repaired = g["qa_flag"].isin(["load_mismatch", "rolling_mismatch", "rolling_filled"])
    # impossible values were blanked by QA; they simply don't plot (hover is on the table/CSV)
    viol = g["is_violation"].fillna(False).astype(bool)
    hover = []
    for (_, r), rep, v in zip(g.iterrows(), repaired, viol):
        lines = [f"<b>{r['period_end']:%b %Y}</b>",
                 f"Reported: {_fmt(r['value'], unit)}" if not rep else
                 f"Used: {_fmt(r['value_clean'], unit)} (reported {_fmt(r['value'], unit)}, "
                 f"replaced: {r['qa_flag'].replace('_', ' ')})"]
        if pd.notna(r["limit"]):
            lines.append(f"Limit in force: {'≥' if is_min else '≤'} {_fmt(r['limit'], unit)}")
            if pd.notna(r["pct_of_limit_clean"]):
                lines.append(f"{r['pct_of_limit_clean']:.0%} of limit")
        else:
            lines.append("No limit this period")
        if pd.notna(r.get("nodi_code")) and pd.isna(r["value"]):
            lines.append(f"Not reported (NODI {r['nodi_code']})")
        if v:
            lines.append("<b>Violation</b>")
        elif pd.notna(r["qa_flag"]) and not rep:
            lines.append(f"QA note: {r['qa_flag'].replace('_', ' ')}")
        hover.append("<br>".join(lines))
    hover = pd.Series(hover, index=g.index)

    normal = ok & ~viol & ~repaired
    fig.add_trace(go.Scatter(
        x=mid[normal], y=y[normal], mode="markers",
        marker=dict(size=6, color=BLUE, line=dict(width=1, color="rgba(252,252,251,0.9)")),
        text=hover[normal], hovertemplate="%{text}<extra></extra>", name="Reported"))
    if (ok & repaired & ~viol).any():
        m = ok & repaired & ~viol
        fig.add_trace(go.Scatter(
            x=mid[m], y=y[m], mode="markers",
            marker=dict(size=8, color="rgba(0,0,0,0)", line=dict(width=1.5, color=BLUE)),
            text=hover[m], hovertemplate="%{text}<extra></extra>", name="Repaired value"))
    if (ok & viol).any():
        m = ok & viol
        fig.add_trace(go.Scatter(
            x=mid[m], y=y[m], mode="markers",
            marker=dict(size=9, color=CRITICAL, symbol="diamond",
                        line=dict(width=1, color="rgba(252,252,251,0.9)")),
            text=hover[m], hovertemplate="%{text}<extra></extra>", name="Violation"))

    _base_layout(fig, 230)
    if forced_value:
        fig.add_annotation(x=0, y=1.02, xref="paper", yref="paper", xanchor="left",
                           yanchor="bottom", showarrow=False, font=dict(size=10, color=MUTED),
                           text=f"Shown in {unit}: % of limit isn't meaningful for this parameter")
        fig.update_layout(margin=dict(t=22))
    fig.update_xaxes(range=x_range)
    ys = pd.concat([y[ok], pd.Series([100.0]) if mode == "pct" else g["limit"].dropna()])
    if len(ys):
        lo, hi = float(ys.min()), float(ys.max())
        if unit.upper() == "SU" and mode != "pct":       # pH: show the band, not from zero
            pad = max(0.3, 0.1 * (hi - lo))
            fig.update_yaxes(range=[lo - pad, hi + pad])
        else:
            fig.update_yaxes(range=[0, hi * 1.08 if hi > 0 else 1])
    fig.update_yaxes(ticksuffix="%" if mode == "pct" else "", title=None)
    return fig


RISK_LABEL = {
    "chronic": "Chronic: typically near the limit",
    "episodic": "Episodic: recent excursions",
    "watch": "Watch: getting close or trending up",
    "check_reporting": "Check reporting: many values exactly at the limit",
    "comfortable": "Comfortable",
    "inactive": "Inactive: no limit in last 12 months",
}


def panel_caption(row) -> str:
    rc = row.get("risk_class", "inactive")
    bits = [RISK_LABEL.get(rc, rc)]
    if rc != "inactive" and pd.notna(row.get("median_recent")):
        if str(row.get("headroom_basis", "")).startswith("margin") and \
                pd.notna(row.get("margin_median_recent")):
            unit = str(row["headroom_basis"])[8:-1]
            bits.append(f"median margin {row['margin_median_recent']:.2g} {unit} (3 yrs)")
        else:
            bits.append(f"median {row['median_recent']:.0%} of limit (3 yrs)")
    if pd.notna(row.get("violations_all")) and row["violations_all"]:
        bits.append(f"{int(row['violations_all'])} violation{'s' if row['violations_all'] != 1 else ''} "
                    f"in window")
    return " · ".join(bits)
