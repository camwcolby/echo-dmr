"""Charts for California Water Boards data: eSMR monitoring and industrial stormwater vs NALs.

Same visual grammar as the rest of the app: blue = reported values, hollow grey = non-detect at
its reporting level, orange = the reference line (here an IGP numeric action level; eSMR has no
limits, so its charts have none), red diamond = a result past the reference.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from .. import viz as V
from ..dw.viz_dw import ND_INK, _std_line
from . import waterboards as W

INFLUENT = "rgba(137,135,129,0.75)"     # influent recedes; effluent is the story
FADED_BLUE = "rgba(42,120,214,0.38)"    # single stormwater results, behind the yearly averages


def _scale(g: pd.DataFrame) -> dict:
    """Pick an honest y-axis for one parameter.

    * Influent and effluent an order of magnitude apart (BOD, TSS): log axis, so both levels and
      their changes stay readable on one panel instead of the effluent lying flat at zero.
    * A few entries far above everything else (a 300 MGD 'flow' at a 0.5 MGD plant): cap the
      axis just above the bulk of the data and pin those points to the top edge as triangles,
      with their true values on hover and counted in the caption, so nothing is hidden.
    """
    v = g["plot_value"]
    v = v[v.notna()]
    out = {"log": False, "cap": None, "n_off": 0, "max": float(v.max()) if len(v) else None}
    if v.empty:
        return out
    med = g[~g["nd"]].groupby("stream")["plot_value"].median()
    if {"Influent", "Effluent"} <= set(med.index) and (v <= 0).mean() <= 0.05:
        ratio = med["Influent"] / med["Effluent"] if med["Effluent"] > 0 else 0
        if ratio >= 8 or (ratio and ratio <= 1 / 8):
            out["log"] = True
            return out
    p99 = float(v.quantile(0.99))
    if p99 > 0 and v.max() > 4 * p99:
        out["cap"] = p99 * 1.3
        out["n_off"] = int((v > out["cap"]).sum())
    return out


def esmr_panel(d: pd.DataFrame, row, x_range=None) -> go.Figure:
    """One parameter: every measured result, effluent in blue, influent in grey, other
    locations (receiving water, groundwater...) as small grey ticks."""
    g = d[(d["parameter"] == row["parameter"]) & (d["units"].fillna("") == W._s(row["units"]))
          & ~d["calculated"] & d["plot_value"].notna()].sort_values("date")
    unit = W._s(row["units"])
    sc = _scale(g)
    cap = sc["cap"]
    if sc["log"] and (g["plot_value"] <= 0).any():
        # a log axis can't show zero: draw reported zeros at the bottom edge as hollow markers
        g = g.copy()
        floor = float(g.loc[g["plot_value"] > 0, "plot_value"].min())
        z = g["plot_value"] <= 0
        g.loc[z, "plot_value"] = floor
        g.loc[z, "nd"] = True
    fig = go.Figure()
    styles = {"Effluent": (V.BLUE, 6), "Influent": (INFLUENT, 5)}
    for stream, h in g.groupby("stream"):
        color, size = styles.get(stream, ("rgba(137,135,129,0.45)", 4))
        off = h["plot_value"] > cap if cap else pd.Series(False, index=h.index)
        det, nd = h[~h["nd"] & ~off], h[h["nd"] & ~off]
        if not det.empty:
            fig.add_trace(go.Scatter(
                x=det["date"], y=det["plot_value"], mode="markers", name=stream,
                marker=dict(size=size, color=color, line=dict(width=0)), text=det["location"],
                hovertemplate=f"{stream} %{{text}}<br>%{{x|%b %d, %Y}}: %{{y:.4~g}} {unit}<extra></extra>"))
        if not nd.empty:
            fig.add_trace(go.Scatter(
                x=nd["date"], y=nd["plot_value"], mode="markers", name=f"{stream} non-detect",
                marker=dict(size=size, color="rgba(0,0,0,0)", line=dict(width=1, color=ND_INK)),
                text=nd["location"],
                hovertemplate=f"{stream} %{{text}}<br>%{{x|%b %d, %Y}}: not detected "
                              f"(< %{{y:.4~g}} {unit})<extra></extra>"))
        if off.any():
            o = h[off]
            fig.add_trace(go.Scatter(
                x=o["date"], y=[cap] * len(o), mode="markers", name=f"{stream} off scale",
                marker=dict(size=9, symbol="triangle-up", color=color), customdata=o["plot_value"],
                text=o["location"],
                hovertemplate=f"{stream} %{{text}}<br>%{{x|%b %d, %Y}}: %{{customdata:.4~g}} {unit}"
                              "<br>off the scale shown (likely a data-entry error)<extra></extra>"))
    # effluent monthly median as a thin line, broken where months are missing
    e = g[g["stream"] == "Effluent"]
    if cap:
        e = e[e["plot_value"] <= cap]
    if len(e) >= 12:
        mm = e.set_index("date")["plot_value"].resample("MS").median().dropna()
        xs, ys = V._gapped(list(mm.index + pd.Timedelta(days=14)), list(mm.values), 75)
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", line=dict(color=V.BLUE, width=1.5),
                                 name="Effluent monthly median", connectgaps=False,
                                 hovertemplate="%{x|%b %Y}: effluent median %{y:.4~g} "
                                               + unit + "<extra></extra>"))
    V._base_layout(fig, 220)
    ytitle = unit + (" (log scale)" if sc["log"] else "")
    fig.update_yaxes(title=dict(text=ytitle, font=dict(size=10, color=V.MUTED)))
    is_ph = str(row["parameter"]).strip().lower().startswith("ph")
    if sc["log"]:
        fig.update_yaxes(type="log")
    elif cap:
        fig.update_yaxes(range=[0, cap * 1.06])
    elif not is_ph:
        fig.update_yaxes(rangemode="tozero")
    if x_range:
        fig.update_xaxes(range=x_range)
    return fig


def esmr_caption(d: pd.DataFrame, row) -> str:
    g = d[(d["parameter"] == row["parameter"]) & ~d["calculated"]]
    parts = []
    for s in ("Influent", "Effluent"):
        v = g.loc[(g["stream"] == s) & ~g["nd"], "plot_value"]
        if len(v):
            parts.append(f"{s.lower()} median {V._fmt(v.median(), W._s(row['units']))} "
                         f"(n={len(v)})")
    sc = _scale(g[g["plot_value"].notna() & (g["units"].fillna("") == W._s(row["units"]))])
    if sc["n_off"]:
        parts.append(f"{sc['n_off']} off-scale (max {V._fmt(sc['max'], W._s(row['units']))}, "
                     "likely data-entry errors)")
    other = sorted(set(g["stream"]) - {"Influent", "Effluent"})
    if other:
        parts.append("also " + ", ".join(o.lower() for o in other))
    return " · ".join(parts)


# ------------------------------------------------------------------ industrial stormwater
NAL_NAME = {"tss": "TSS", "og": "Oil & grease", "ph": "pH", "cod": "COD", "bod": "BOD",
            "nn": "Nitrate + nitrite N", "tp": "Total phosphorus"}


def _margin_label(fig, y, text, where="middle"):
    """Label in the right margin; where='top'/'bottom' sets it just above/below its line so two
    close lines (O&G 15 and 25) don't overprint."""
    fig.add_annotation(x=1.01, xref="paper", y=y, text=text, showarrow=False, xanchor="left",
                       align="left", yanchor=where, font=dict(size=9, color=V.MUTED))


def sw_panels(m: pd.DataFrame) -> list[str]:
    """NAL parameters this site sampled, required three (TSS, O&G, pH) first, then the rest by
    how far the worst annual average got past its NAL."""
    t = W.nal_table(m)
    if t.empty:
        return []
    t["ratio"] = t["average"] / t["annual_nal"]
    worst = t.groupby("nal_key")["ratio"].max().fillna(0)
    req = [k for k in ("tss", "og", "ph") if k in worst.index]
    rest = [k for k in worst.sort_values(ascending=False).index if k not in req]
    return req + rest


def sw_panel(m: pd.DataFrame, key: str, x_range=None) -> go.Figure | None:
    n = W.IGP_NAL[key]
    g = m[m["nal_key"] == key].dropna(subset=["plot_value"]).sort_values("date").copy()
    if g.empty:
        return None
    ph = key == "ph"
    g["v"] = g["plot_value"] if ph else g["plot_value"] * g["to_mgl"]
    g = g.dropna(subset=["v"])
    unit = "SU" if ph else "mg/L"
    if ph:
        over = (g["v"] < n["lo"]) | (g["v"] > n["hi"])
    else:
        over = g["v"] > (n.get("inst") or float("inf"))
    fig = go.Figure()
    det, nd = g[~g["nd"]], g[g["nd"]]
    if not nd.empty:
        fig.add_trace(go.Scatter(
            x=nd["date"], y=nd["v"], mode="markers", name="Non-detect",
            marker=dict(size=6, color="rgba(0,0,0,0)", line=dict(width=1, color=ND_INK)),
            text=nd["MONITORING_LOCATION_NAME"],
            hovertemplate="%{text} %{x|%b %d, %Y}: not detected (< %{y:.3~g} " + unit
                          + ")<extra></extra>"))
    dot = V.BLUE if ph else FADED_BLUE      # pH has no yearly average, so its results lead
    for mask, color, sym, nm in [(~over.loc[det.index], dot, "circle", "Result"),
                                 (over.loc[det.index], V.CRITICAL, "diamond", "Past instantaneous NAL")]:
        if mask.any():
            fig.add_trace(go.Scatter(
                x=det["date"][mask], y=det["v"][mask], mode="markers", name=nm,
                marker=dict(size=6 if sym == "circle" else 10, color=color, symbol=sym,
                            line=dict(width=0 if sym == "circle" else 1, color="rgba(252,252,251,0.9)")),
                text=det["MONITORING_LOCATION_NAME"][mask],
                hovertemplate="%{text} %{x|%b %d, %Y}: %{y:.3~g} " + unit +
                              ("<br><b>Past the instantaneous NAL</b>" if sym == "diamond" else "")
                              + "<extra></extra>"))
    if not ph:
        # reporting-year average (IGP year = Jul 1 - Jun 30) as a bold segment across the whole
        # year, red when over the annual NAL, labeled with its value: the annual NAL applies to
        # this average, so it's the mark that should read first; single results recede behind it
        for yr, h in g.groupby("REPORTING_YEAR"):
            a = h["v"].mean()
            y0 = pd.Timestamp(year=int(yr), month=7, day=1)
            over_a = a > n["annual"]
            color = V.CRITICAL if over_a else V.BLUE
            fig.add_trace(go.Scatter(
                x=[y0 + pd.Timedelta(days=15), y0 + pd.Timedelta(days=350)], y=[a, a],
                mode="lines", line=dict(color=color, width=5), name="Reporting-year average",
                hovertemplate=f"Reporting year {yr} (Jul {yr}-Jun {int(yr) + 1}): average "
                              f"{a:.3g} {unit} from {len(h)} results"
                              + (" <b>over the annual NAL</b>" if over_a else "") + "<extra></extra>"))
            fig.add_annotation(x=y0 + pd.Timedelta(days=182), y=a, text=f"{a:.3g}", showarrow=False,
                               yanchor="bottom", yshift=3, font=dict(size=9, color=color))
        # NAL labels sit in the right margin, outside the data, so the yearly values never
        # collide with them
        fig.add_hline(y=n["annual"], line=dict(color=V.LIMIT, width=2))
        _margin_label(fig, n["annual"], f"annual<br>NAL {V._fmt(n['annual'])}",
                      "top" if n.get("inst") else "middle")
        if n.get("inst"):
            fig.add_hline(y=n["inst"], line=dict(color=V.LIMIT, width=1, dash="dot"))
            _margin_label(fig, n["inst"], f"instant.<br>NAL {V._fmt(n['inst'])}", "bottom")
        hi = max(g["v"].max(), n.get("inst") or n["annual"], n["annual"])
    else:
        for lvl in (n["lo"], n["hi"]):
            fig.add_hline(y=lvl, line=dict(color=V.LIMIT, width=2))
            _margin_label(fig, lvl, f"NAL {lvl:g}")
        hi = max(g["v"].max(), n["hi"]) + 0.5
    V._base_layout(fig, 220)
    fig.update_layout(margin=dict(r=58))
    fig.update_yaxes(title=dict(text=unit, font=dict(size=10, color=V.MUTED)),
                     range=[(min(g["v"].min(), n["lo"]) - 0.5) if ph else 0, hi * (1 if ph else 1.1)])
    if x_range:
        fig.update_xaxes(range=x_range)
    return fig


def sw_caption(m: pd.DataFrame, key: str) -> str:
    t = W.nal_table(m)
    t = t[t["nal_key"] == key]
    if t.empty:
        return ""
    n = W.IGP_NAL[key]
    if key == "ph":
        hits = int(t["inst_hits"].sum())
        return (f"{int(t['results'].sum())} results over {len(t)} reporting years · "
                f"{hits} outside {n['lo']:g}-{n['hi']:g}")
    over = t[t["over_annual"] == True]  # noqa: E712
    s = (f"{int(t['results'].sum())} results over {len(t)} reporting years · annual average "
         f"over the NAL in {len(over)} year{'s' if len(over) != 1 else ''}"
         + (f" ({', '.join(str(int(y)) for y in over['year'])})" if len(over) else ""))
    if n.get("inst"):
        s += f" · {int(t['inst_hits'].sum())} results past the instantaneous NAL"
    return s
