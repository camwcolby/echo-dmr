"""Drinking water charts. Same visual language as the wastewater side:
blue = results, orange = the federal standard, red diamond = above the standard (or a
health-based violation), hollow grey = non-detect plotted at its reporting level."""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from .. import viz as V
from . import standards as S

ND_INK = "rgba(137,135,129,0.85)"


def _std_line(fig, value, label):
    fig.add_hline(y=value, line=dict(color=V.LIMIT, width=2))
    fig.add_annotation(x=1, xref="paper", y=value, text=label, showarrow=False, xanchor="right",
                       yanchor="bottom", yshift=1, font=dict(size=10, color=V.MUTED))


# ------------------------------------------------------------------ violations
def violations_timeline(v: pd.DataFrame, x_range=None) -> go.Figure | None:
    if v.empty or "start" not in v or v["start"].isna().all():
        return None
    d = v.dropna(subset=["start"]).copy()
    d["row"] = d["contaminant"].fillna("Unspecified")
    order = d.groupby("row").size().sort_values().index.tolist()   # most frequent at top
    fig = go.Figure()
    for hb, color, sym, name in [(False, ND_INK, "circle", "Not health-based"),
                                 (True, V.CRITICAL, "diamond", "Health-based")]:
        g = d[d["health_based"] == hb]
        if g.empty:
            continue
        # compliance period as a thin bar, start marker on it
        xs, ys = [], []
        for _, r in g.iterrows():
            if pd.notna(r.get("end")) and r["end"] > r["start"]:
                xs += [r["start"], r["end"], None]
                ys += [r["row"], r["row"], None]
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", line=dict(color=color, width=3),
                                 opacity=0.35, hoverinfo="skip", showlegend=False))
        hover = [
            f"<b>{r['row']}</b><br>{r.get('category', '')}<br>"
            f"{r['start']:%b %d, %Y}" + (f" to {r['end']:%b %d, %Y}" if pd.notna(r.get('end')) else "")
            + f"<br>Status: {r.get('status') or 'n/a'}"
            + (f"<br>Measured {V._fmt(r['viol_measure'])} vs MCL {V._fmt(r['state_mcl'])} "
               f"{r.get('unit_of_measure') or ''}" if pd.notna(r.get('viol_measure')) else "")
            + f"<br>{name}"
            for _, r in g.iterrows()]
        fig.add_trace(go.Scatter(
            x=g["start"], y=g["row"], mode="markers", name=name,
            marker=dict(size=9, color=color, symbol=sym, line=dict(width=1, color="rgba(252,252,251,0.9)")),
            text=hover, hovertemplate="%{text}<extra></extra>"))
    V._base_layout(fig, max(220, 26 * len(order) + 90))
    fig.update_layout(showlegend=True, legend=dict(orientation="h", y=1.02, yanchor="bottom",
                                                   x=0, xanchor="left"))
    fig.update_yaxes(categoryorder="array", categoryarray=order, gridcolor="rgba(0,0,0,0)")
    if x_range:
        fig.update_xaxes(range=x_range)
    return fig


def violation_summary(v: pd.DataFrame) -> pd.DataFrame:
    if v.empty:
        return pd.DataFrame()
    d = v.copy()
    d["year"] = d["start"].dt.year
    t = (d.groupby(["contaminant", "category"], dropna=False)
          .agg(violations=("start", "size"), health_based=("health_based", "sum"),
               open_now=("status", lambda s: int((s == "Open").sum())),
               first=("start", "min"), last=("start", "max"))
          .reset_index().sort_values(["health_based", "violations"], ascending=False))
    t["first"] = t["first"].dt.date
    t["last"] = t["last"].dt.date
    return t


# ------------------------------------------------------------------ lead & copper
def lead_copper_panel(lc: pd.DataFrame, analyte: str, x_range=None) -> go.Figure | None:
    g = lc[lc["analyte"] == analyte].dropna(subset=["value_mg_l"])
    if g.empty:
        return None
    std = S.LEAD_AL if analyte == "Lead" else S.COPPER_AL
    scale, unit = (1000, "µg/L") if analyte == "Lead" else (1, "mg/L")
    y = g["value_mg_l"] * scale
    al = std.value * scale
    over = y > al
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=g["date"], y=y, mode="lines", line=dict(color=V.BLUE, width=1),
                             opacity=0.45, hoverinfo="skip"))
    for m, color, sym in [(~over, V.BLUE, "circle"), (over, V.CRITICAL, "diamond")]:
        if m.any():
            fig.add_trace(go.Scatter(
                x=g["date"][m], y=y[m], mode="markers",
                marker=dict(size=8 if sym == "circle" else 10, color=color, symbol=sym,
                            line=dict(width=1, color="rgba(252,252,251,0.9)")),
                hovertemplate="%{x|%b %Y}: 90th percentile %{y:.3~g} " + unit +
                              ("<br><b>Above action level</b>" if sym == "diamond" else "") +
                              "<extra></extra>"))
    _std_line(fig, al, f"action level {al:g} {unit}")
    V._base_layout(fig, 250, f"{analyte}, 90th percentile ({unit})")
    fig.update_yaxes(range=[0, max(float(y.max()), al) * 1.12])
    if x_range:
        fig.update_xaxes(range=x_range)
    return fig


# ------------------------------------------------------------------ state sample results
def prepare_results(res: pd.DataFrame) -> pd.DataFrame:
    """Attach the matching federal standard and convert values to the standard's unit."""
    if res.empty:
        return res
    d = res.copy()
    stds = d["analyte"].map(lambda a: S.match(a) or S.match(a, S.PFAS))
    d["std_key"] = stds.map(lambda s: s.key if s else None)
    d["std_value"] = stds.map(lambda s: s.value if s else np.nan)
    d["std_unit"] = stds.map(lambda s: s.unit if s else None)
    d["std_basis"] = stds.map(lambda s: s.basis if s else None)
    d["std_kind"] = stds.map(lambda s: s.kind if s else None)
    d["v"] = [S.convert(v, u, su) if isinstance(su, str) and pd.notna(v) else np.nan
              for v, u, su in zip(d["value"], d["unit"], d["std_unit"])]
    rl = d["reporting_level"] if "reporting_level" in d else pd.Series(np.nan, index=d.index)
    d["rl"] = [S.convert(v, u, su) if isinstance(su, str) and pd.notna(v) else np.nan
               for v, u, su in zip(rl, d["unit"], d["std_unit"])]
    d["nd"] = d["less_than"].fillna(False).astype(bool) | (d["value"].fillna(0) <= 0)
    d["pct_std"] = np.where(d["nd"], np.nan, d["v"] / d["std_value"])
    return d


def running_avg(g: pd.DataFrame) -> pd.Series:
    """Highest location's running annual average of quarterly means (non-detects as 0)."""
    x = g.dropna(subset=["collected"]).copy()
    if x.empty:
        return pd.Series(dtype=float)
    x["val"] = np.where(x["nd"], 0.0, x["v"])
    x["q"] = x["collected"].dt.to_period("Q")
    loc = x["sampling_point"] if "sampling_point" in x else pd.Series("all", index=x.index)
    x["loc"] = loc.fillna("all")
    qm = x.groupby(["loc", "q"])["val"].mean().unstack("loc").sort_index()
    if qm.empty:
        return pd.Series(dtype=float)
    qm = qm.reindex(pd.period_range(qm.index.min(), qm.index.max(), freq="Q"))
    raa = qm.rolling(4, min_periods=4).mean().max(axis=1).dropna()
    raa.index = raa.index.to_timestamp(how="end").normalize()
    return raa


def analyte_panels(d: pd.DataFrame) -> pd.DataFrame:
    """One row per analyte with a federal standard, riskiest first."""
    s = d[d["std_key"].notna()]
    if s.empty:
        return pd.DataFrame()
    t = (s.groupby(["analyte", "std_key", "std_value", "std_unit", "std_basis", "std_kind"])
          .agg(samples=("v", "size"), detects=("nd", lambda x: int((~x).sum())),
               max_value=("v", "max"), max_pct=("pct_std", "max"),
               first=("collected", "min"), last=("collected", "max"))
          .reset_index())
    return t.sort_values("max_pct", ascending=False, na_position="last").reset_index(drop=True)


BASIS_TEXT = {"single": "compared sample by sample",
              "RAA": "compliance on the running annual average",
              "LRAA": "compliance on each location's running annual average",
              "P90": "90th percentile vs action level"}


def analyte_panel(d: pd.DataFrame, row, x_range=None) -> go.Figure:
    g = d[(d["analyte"] == row["analyte"])].dropna(subset=["collected"]).sort_values("collected")
    std, unit = float(row["std_value"]), row["std_unit"]
    fig = go.Figure()
    det, nd = g[~g["nd"] & g["v"].notna()], g[g["nd"]]
    if not nd.empty and nd["rl"].notna().any():
        fig.add_trace(go.Scatter(
            x=nd["collected"], y=nd["rl"], mode="markers", name="Non-detect",
            marker=dict(size=6, color="rgba(0,0,0,0)", line=dict(width=1, color=ND_INK)),
            hovertemplate="%{x|%b %d, %Y}: not detected (< %{y:.3~g} " + unit + ")<extra></extra>"))
    if not det.empty:
        over = det["v"] > std
        for m, color, sym, nm in [(~over, V.BLUE, "circle", "Detected"),
                                  (over, V.CRITICAL, "diamond", "Above standard")]:
            if m.any():
                sp = det.get("sampling_point", pd.Series("", index=det.index)).fillna("")
                fig.add_trace(go.Scatter(
                    x=det["collected"][m], y=det["v"][m], mode="markers", name=nm,
                    marker=dict(size=7 if sym == "circle" else 10, color=color, symbol=sym,
                                line=dict(width=1, color="rgba(252,252,251,0.9)")),
                    text=sp[m],
                    hovertemplate="%{x|%b %d, %Y}: %{y:.3~g} " + unit + "<br>%{text}" +
                                  ("<br><b>Above the federal standard</b>" if sym == "diamond" else "")
                                  + "<extra></extra>"))
    if row["std_basis"] in ("RAA", "LRAA"):
        raa = running_avg(g)
        if len(raa):
            fig.add_trace(go.Scatter(
                x=raa.index, y=raa.values, mode="lines", line=dict(color=V.BLUE, width=2.5),
                name="Running annual avg",
                hovertemplate="%{x|%b %Y}: running annual avg %{y:.3~g} " + unit +
                              " (highest location)<extra></extra>"))
    _std_line(fig, std, f"{row['std_kind']} {V._fmt(std, unit)}")
    V._base_layout(fig, 230)
    hi = max([std] + [float(x) for x in (det["v"].max() if not det.empty else std,
                                         nd["rl"].max() if not nd.empty and nd["rl"].notna().any() else 0)])
    fig.update_yaxes(range=[0, hi * 1.12])
    if x_range:
        fig.update_xaxes(range=x_range)
    return fig


def other_analytes(d: pd.DataFrame) -> pd.DataFrame:
    o = d[d["std_key"].isna()]
    if o.empty:
        return pd.DataFrame()
    t = (o.groupby(["analyte", "unit"], dropna=False)
          .agg(samples=("value", "size"), detects=("nd", lambda x: int((~x).sum())),
               max_detected=("value", lambda s: s[~o.loc[s.index, "nd"]].max()),
               last=("collected", "max")).reset_index())
    t["last"] = t["last"].dt.date
    return t.sort_values(["detects", "samples"], ascending=False)


# ------------------------------------------------------------------ UCMR / PFAS
def pfas_panel(u: pd.DataFrame, std: S.Standard, x_range=None) -> go.Figure | None:
    g = u[u["pfas_key"] == std.key].dropna(subset=["date"])
    if g.empty:
        return None
    fig = go.Figure()
    nd, det = g[~g["detected"]], g[g["detected"]]
    if not nd.empty:
        fig.add_trace(go.Scatter(
            x=nd["date"], y=nd["mrl_ng_l"], mode="markers", name="Non-detect",
            marker=dict(size=6, color="rgba(0,0,0,0)", line=dict(width=1, color=ND_INK)),
            hovertemplate="%{x|%b %d, %Y}: not detected (< %{y:.3~g} ng/L)<extra></extra>"))
    if not det.empty:
        over = det["value_ng_l"] > std.value
        for m, color, sym in [(~over, V.BLUE, "circle"), (over, V.CRITICAL, "diamond")]:
            if m.any():
                fig.add_trace(go.Scatter(
                    x=det["date"][m], y=det["value_ng_l"][m], mode="markers",
                    marker=dict(size=8 if sym == "circle" else 10, color=color, symbol=sym,
                                line=dict(width=1, color="rgba(252,252,251,0.9)")),
                    text=det.get("SamplePointName", pd.Series("", index=det.index))[m],
                    hovertemplate="%{x|%b %d, %Y}: %{y:.3~g} ng/L<br>%{text}" +
                                  ("<br><b>Above the 2024 MCL</b>" if sym == "diamond" else "") +
                                  "<extra></extra>"))
    _std_line(fig, std.value, f"MCL {std.value:g} ng/L")
    V._base_layout(fig, 230)
    top = max(std.value, float(g["value_ng_l"].max() if det.shape[0] else 0),
              float(g["mrl_ng_l"].max() if g["mrl_ng_l"].notna().any() else 0))
    fig.update_yaxes(range=[0, top * 1.15])
    if x_range:
        fig.update_xaxes(range=x_range)
    return fig


def hi_panel(hi: pd.DataFrame, x_range=None) -> go.Figure | None:
    if hi.empty:
        return None
    over = hi["hazard_index"] > 1
    fig = go.Figure()
    for m, color, sym in [(~over, V.BLUE, "circle"), (over, V.CRITICAL, "diamond")]:
        if m.any():
            fig.add_trace(go.Scatter(
                x=hi["date"][m], y=hi["hazard_index"][m], mode="markers",
                marker=dict(size=8 if sym == "circle" else 10, color=color, symbol=sym),
                hovertemplate="%{x|%b %d, %Y}: hazard index %{y:.2f}<extra></extra>"))
    _std_line(fig, 1, "hazard index 1")
    V._base_layout(fig, 230)
    fig.update_yaxes(range=[0, max(1.0, float(hi["hazard_index"].max())) * 1.15])
    if x_range:
        fig.update_xaxes(range=x_range)
    return fig


def ucmr_table(u: pd.DataFrame) -> pd.DataFrame:
    if u.empty:
        return pd.DataFrame()
    t = (u.groupby("Contaminant", observed=True)
          .agg(samples=("value", "size"), detects=("detected", "sum"),
               max_detected_ng_l=("value_ng_l", lambda s: s[u.loc[s.index, "detected"]].max()),
               first=("date", "min"), last=("date", "max")).reset_index())
    t["first"], t["last"] = t["first"].dt.date, t["last"].dt.date
    return t.sort_values(["detects", "Contaminant"], ascending=[False, True])
