"""Wastewater (NPDES / DMR) mode of the explorer."""
from __future__ import annotations

import re
import time
from datetime import date

import pandas as pd
import streamlit as st

from echo_pull import analysis as A
from echo_pull import client, normalize, qa, viz

NPDES_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{7}$")   # e.g. TN0020117, CA0107409, MAG580001
PLOT_CFG = {"displaylogo": False, "modeBarButtonsToRemove": ["lasso2d", "select2d"]}


# ------------------------------------------------------------------ data
@st.cache_data(ttl=7 * 24 * 3600, show_spinner=False, max_entries=200)
def fetch_year(npdes_id: str, year: int, _cache_day: str) -> pd.DataFrame:
    return client.fetch_dmr_year(npdes_id, year)


@st.cache_data(ttl=7 * 24 * 3600, show_spinner=False, max_entries=500)
def facility_name(npdes_id: str) -> str | None:
    try:
        r = client._get(client.CWA_FACILITIES, {"output": "JSON", "p_pid": npdes_id,
                                                "responseset": 1}, retries=1, timeout=30)
        qid = r.json().get("Results", {}).get("QueryID")
        if not qid:
            return None
        f = client._get(client.CWA_QID, {"output": "JSON", "qid": qid, "pageno": 1},
                        retries=1, timeout=30).json()
        facs = f.get("Results", {}).get("Facilities", [])
        return f"{facs[0].get('CWPName')}, {facs[0].get('CWPCity')}, {facs[0].get('CWPState')}" \
            if facs else None
    except Exception:
        return None


@st.cache_data(show_spinner=False, max_entries=20)
def analyze(raw: pd.DataFrame, npdes_id: str):
    r = raw.copy()
    if "npdes_id" not in r.columns:
        r["npdes_id"] = npdes_id
    df = qa.apply(normalize.normalize(r))
    h = A.headroom(df)
    return df, h, A.loads(df), viz.panel_list(df, h)


def pull(npdes: str, years: int):
    today = date.today()
    start = pd.Timestamp(today) - pd.DateOffset(years=int(years))
    yrs = list(range(start.year, today.year + 1))
    bar = st.progress(0.0, text="Starting...")
    frames, failed = [], []
    for i, y in enumerate(yrs):
        bar.progress(i / len(yrs), text=f"Pulling {y} from EPA ECHO...")
        try:
            df = fetch_year(npdes, y, today.isoformat() if y == today.year else "")
        except client.EchoError:
            failed.append(y)
            continue
        if not df.empty:
            frames.append(df)
        time.sleep(0.3)
    bar.empty()
    if not frames:
        return None, start, failed
    out = pd.concat(frames, ignore_index=True).drop_duplicates()
    out.columns = [c.strip().lower() for c in out.columns]
    if "monitoring_period_end_date" in out.columns:
        keep = pd.to_datetime(out["monitoring_period_end_date"], errors="coerce") > start
        out = out[keep | keep.isna()].reset_index(drop=True)
    return out, start, failed




def render():
    # ------------------------------------------------------------------ header + form
    st.caption("Discharge Monitoring Report data for one NPDES permit, from EPA ECHO. Public data; "
               "nothing you enter is stored.")

    with st.form("pull"):
        c1, c2, c3 = st.columns([3, 1, 4])
        npdes_in = c1.text_input("NPDES permit ID", placeholder="TN0020117").strip().upper()
        years_in = c2.number_input("Years", min_value=1, max_value=15, value=10, step=1)
        go = st.form_submit_button("Get data", type="primary")

    if go:
        if not NPDES_RE.match(npdes_in):
            st.error("That doesn't look like an NPDES ID: two letters then seven letters/digits, "
                     "like TN0020117. Look one up at https://echo.epa.gov/facilities/facility-search")
            st.stop()
        raw, start, failed = pull(npdes_in, years_in)
        if raw is None:
            msg = "ECHO returned no DMR data for that permit."
            if failed:
                msg += f" EPA's service also failed for {', '.join(map(str, failed))}; try again shortly."
            st.error(msg + " Check the ID at https://echo.epa.gov/effluent-charts")
            st.session_state.pop("pulled", None)
            st.stop()
        st.session_state["pulled"] = dict(npdes=npdes_in, raw=raw, start=start, failed=failed,
                                          name=facility_name(npdes_in), years=int(years_in))

    P = st.session_state.get("pulled")
    if not P:
        st.stop()

    npdes, raw, start, failed = P["npdes"], P["raw"], P["start"], P["failed"]
    with st.spinner("Checking data quality and analyzing..."):
        df, h, L, panels = analyze(raw, npdes)

    end = df["period_end"].max()
    x_range = [start, end + pd.Timedelta(days=20)]
    st.subheader(f"{npdes}" + (f": {P['name']}" if P["name"] else ""))
    st.write(f"{df['period_end'].min():%b %Y} to {end:%b %Y}, "
             f"{df['period_end'].dt.to_period('M').nunique()} months with data · "
             f"{int(df['is_violation_echo'].sum())} numeric violations on ECHO's record · "
             f"{int(df['qa_flag'].isin(['load_mismatch', 'rolling_mismatch', 'rolling_filled', 'impossible_value']).sum())} "
             "values repaired before charting (data-entry errors)")
    if failed:
        st.warning(f"EPA's service failed for {', '.join(map(str, failed))}, so those years are "
                   "missing. Click Get data again to fill them in.")

    # A remembered selector, not st.tabs: tabs jump back to the first one whenever a filter changes.
    section = st.segmented_control("Section", ["Flow & loads", "Permit limits", "Download"],
                                   default="Flow & loads", key="section",
                                   label_visibility="collapsed") or "Flow & loads"

    # ------------------------------------------------------------------ flow & loads
    if section == "Flow & loads":
        for note in L["notes"]:
            st.info(note)
        cols = st.columns(3)
        any_chart = False
        for col, (key, label, unit) in zip(cols, viz.LOAD_PANELS):
            fig = viz.annual_panel(L, key, label, unit, x_range, start)
            with col:
                if fig is None:
                    st.markdown(f"**{label}**")
                    st.caption("Not in this permit's DMRs.")
                else:
                    any_chart = True
                    st.plotly_chart(fig, config=PLOT_CFG, theme="streamlit", width="stretch")
        if any_chart:
            st.caption("Dots: monthly values. Line: annual average of the monthly values; hollow "
                       "markers are partial years. Influent loads are influent monthly-average "
                       "concentration × flow × 8.34. Flow is influent where reported, otherwise effluent.")
            t = viz.annual_table(L, start)
            st.dataframe(t.style.format("{:,.2f}", subset=[c for c in t.columns if "Flow" in c and "avg" in c])
                         .format("{:,.0f}", subset=[c for c in t.columns if "load" in c and "avg" in c]),
                         width="stretch")

    # ------------------------------------------------------------------ permit limits
    if section == "Permit limits":
        if panels.empty:
            st.info("No permit limits in this permit's DMRs for this window.")
        else:
            f1, f2, f3, f4 = st.columns([2, 2, 2, 3])
            view = f1.radio("Show values as", ["Value vs limit", "% of limit in force"],
                            help="'% of limit in force' divides each month by the limit that applied "
                                 "that month, so seasonal limits and different parameters share one scale.")
            scope = f2.radio("Limits", ["Needs attention", "All active", "All, incl. inactive"],
                             index=1)
            locs = sorted(panels["monitoring_location"].dropna().unique())
            loc_sel = f3.multiselect("Monitoring location", locs, default=locs)
            params = sorted(panels["parameter"].dropna().unique())
            par_sel = f4.multiselect("Parameter", params, placeholder="All parameters")

            p = panels
            if scope == "Needs attention":
                p = p[p["risk_class"].isin(["chronic", "episodic", "watch", "check_reporting"])]
            elif scope == "All active":
                p = p[p["risk_class"] != "inactive"]
            p = p[p["monitoring_location"].isin(loc_sel)]
            if par_sel:
                p = p[p["parameter"].isin(par_sel)]

            st.caption(f"{len(p)} of {len(panels)} limits, riskiest first. Blue: reported value "
                       "(hollow = repaired data-entry error). Orange step: the limit in force each "
                       "month, so seasonal and renewal changes show where they applied. Red diamond: "
                       "violation. Hover any point for details; drag to zoom, double-click to reset.")
            mode = "pct" if view.startswith("%") else "value"
            rows = [p.iloc[i:i + 3] for i in range(0, len(p), 3)]
            k = 0
            for chunk in rows:                      # one st.columns row per 3 panels: rows stay aligned
                for col, (_, r) in zip(st.columns(3), chunk.iterrows()):
                    k += 1
                    with col:
                        loc = r["monitoring_location"]
                        st.markdown(f"**{r['parameter']}**  \n{r['stat_base']} · {r['unit']} · "
                                    f"{loc}{'' if pd.isna(r['outfall']) else ' ' + str(r['outfall'])}")
                        fig = viz.limit_panel(df, r, x_range, mode)
                        if fig is not None:
                            st.plotly_chart(fig, config=PLOT_CFG, theme="streamlit", width="stretch",
                                            key=f"lim{k}-{mode}")
                        story = viz.limit_story(viz._select(df, r), str(r["unit"]),
                                                df["period_end"].min(), end)
                        st.caption(viz.panel_caption(r) + ("  \n" + " · ".join(story) if story else ""))

    # ------------------------------------------------------------------ download
    if section == "Download":
        st.download_button(
            "Download raw ECHO CSV", raw.to_csv(index=False).encode("utf-8"),
            file_name=f"echo_dmr_{npdes}_{start:%Y%m}_{date.today():%Y%m}.csv",
            mime="text/csv", type="primary")
        st.caption("Exactly as ECHO provides it, one row per reported value. Use "
                   "`dmr_value_standard_units` with `standard_unit_desc` and "
                   "`limit_value_standard_units` so values and limits share units.")
        keep = ["period_end", "outfall", "monitoring_location", "parameter", "stat_base", "unit",
                "value", "value_clean", "limit", "limit_direction", "pct_of_limit_clean",
                "is_violation", "qa_flag", "nodi_code"]
        clean = df[[c for c in keep if c in df.columns]].copy()
        clean["period_end"] = clean["period_end"].dt.date
        st.download_button(
            "Download cleaned CSV (what the charts use)", clean.to_csv(index=False).encode("utf-8"),
            file_name=f"echo_dmr_{npdes}_clean_{start:%Y%m}_{date.today():%Y%m}.csv", mime="text/csv")
        st.caption("`value_clean` repairs gross data-entry errors (load vs concentration × flow, "
                   "rolling averages); `qa_flag` says why. Raw `value` is unchanged.")
        st.download_button(
            "Download annual flow & loads", viz.annual_table(L, start).to_csv().encode("utf-8"),
            file_name=f"echo_annual_loads_{npdes}.csv", mime="text/csv")
