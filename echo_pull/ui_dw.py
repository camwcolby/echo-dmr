"""Drinking water (PWSID) mode of the explorer."""
from __future__ import annotations

import re
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from . import viz as V
from .dw import dww, federal, standards as S, tn_tableau as TN, ucmr, viz_dw as DV

PWSID_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{7}$")
PLOT_CFG = {"displaylogo": False, "modeBarButtonsToRemove": ["lasso2d", "select2d"]}
TTL = 7 * 24 * 3600


# ------------------------------------------------------------------ cached data
@st.cache_data(ttl=TTL, show_spinner=False, max_entries=200)
def get_federal(pwsid: str):
    out, errs = {}, []
    for name, fn in [("profile", federal.profile), ("violations", federal.violations),
                     ("lead_copper", federal.lead_copper)]:
        try:
            out[name] = fn(pwsid)
        except Exception as e:  # noqa: BLE001
            out[name] = {} if name == "profile" else pd.DataFrame()
            errs.append(f"{name}: {str(e)[:160]}")
    out["errors"] = errs
    return out


@st.cache_data(ttl=TTL, show_spinner=False, max_entries=100)
def search(name: str, state: str):
    return federal.search_systems(name=name, state=state)


@st.cache_data(ttl=TTL, show_spinner=False, max_entries=50)
def get_state(pwsid: str, years: int, max_samples: int, name: str = ""):
    if pwsid[:2] == "TN":
        return get_tn(pwsid, years, name)
    sess, base = dww.open_state(pwsid[:2])
    sysid = dww.find_system(pwsid, base, sess)
    bar = st.progress(0.0, text="Reading sample results from the state's site...")
    res, info = dww.all_results(base, sysid, date.today() - timedelta(days=365 * years),
                                max_samples=max_samples, sess=sess,
                                progress=lambda f: bar.progress(f, text=f"Reading sample results... {f:.0%}"))
    bar.empty()
    info["base"] = base
    return res, info


def get_tn(pwsid: str, years: int, name: str):
    """Tennessee: one CSV export from TDEC's Tableau dashboard holds the system's whole record."""
    with st.spinner("Asking Tennessee's dashboard for this system's Chem/Rad samples..."):
        raw, label = TN.chem_rad(pwsid, name)
    full = TN.to_results(raw)
    res = full[full["collected"] >= pd.Timestamp(date.today() - timedelta(days=365 * years))]
    info = {"tn": True, "rows_found": len(full), "rows_kept": len(res), "label": label.strip(),
            "first": full["collected"].min(),
            "base": "data.tn.gov (TDEC Drinking Water Watch dashboard)"}
    return res.reset_index(drop=True), info


@st.cache_data(ttl=TTL, show_spinner=False, max_entries=100)
def get_ucmr(pwsid: str):
    return ucmr.load_pws(pwsid)


def _nice(name: str) -> str:
    """'NITRATE' -> 'Nitrate', but keep acronyms like TTHM, HAA5, PFOA as they are."""
    n = str(name or "")
    return n if len(n) <= 6 or n.isdigit() else n.title().replace("(Haa5)", "(HAA5)")


# ------------------------------------------------------------------ page
def render():
    st.caption("Federal drinking water data (SDWIS violations, lead & copper, UCMR 5 PFAS) plus "
               "sample-level results from the state's Drinking Water Watch site where one exists. "
               "Standards shown are federal (40 CFR 141); states may be stricter. Charts are a "
               "screen, not a compliance determination.")

    with st.expander("Don't know the PWSID? Search by name"):
        c1, c2, c3 = st.columns([3, 1, 1])
        q_name = c1.text_input("System name contains", key="dw_q_name")
        q_state = c2.text_input("State", max_chars=2, key="dw_q_state").upper()
        if c3.button("Search", key="dw_q_go") and (q_name or q_state):
            try:
                st.session_state["dw_hits"] = search(q_name.strip(), q_state.strip())
            except Exception as e:  # noqa: BLE001
                st.error(f"EPA search failed: {str(e)[:200]}")
        hits = st.session_state.get("dw_hits")
        if hits is not None:
            if hits.empty:
                st.info("No systems matched.")
            else:
                st.dataframe(hits.head(50), hide_index=True, width="stretch")
                st.caption("Copy a PWSID into the box below.")

    with st.form("dw_pull"):
        c1, c2, _ = st.columns([3, 1, 4])
        pwsid = c1.text_input("Public water system ID (PWSID)", placeholder="TN0000700",
                              key="dw_pwsid_in").strip().upper()
        years = c2.number_input("Years", min_value=1, max_value=15, value=10, step=1, key="dw_years")
        go = st.form_submit_button("Get data", type="primary")
    if go:
        if not PWSID_RE.match(pwsid):
            st.error("A PWSID is two letters then seven letters/digits, like TN0000700.")
            st.stop()
        st.session_state["dw"] = {"pwsid": pwsid, "years": int(years)}
        st.session_state.pop("dw_state_req", None)

    P = st.session_state.get("dw")
    if not P:
        st.stop()
    pwsid, years = P["pwsid"], P["years"]
    start = pd.Timestamp(date.today() - timedelta(days=365 * years))
    x_range = [start, pd.Timestamp(date.today()) + pd.Timedelta(days=20)]

    with st.spinner("Pulling federal data from EPA..."):
        F = get_federal(pwsid)
    prof, viol, lc = F["profile"], F["violations"], F["lead_copper"]
    if F["errors"]:
        st.warning("Some EPA requests failed (try again shortly): " + "; ".join(F["errors"]))
    if not prof and viol.empty and lc.empty:
        st.error("EPA has no record of that PWSID. Check it with the name search above.")
        st.stop()

    # ---- header
    title = f"{pwsid}" + (f": {prof.get('pws_name')}" if prof.get("pws_name") else "")
    st.subheader(title)
    bits = []
    if prof:
        if pd.notna(prof.get("population_served_count")):
            bits.append(f"serves {prof['population_served_count']:,.0f} people")
        for k in ("pws_type", "source"):
            if prof.get(k):
                bits.append(str(prof[k]).lower() if k == "source" else str(prof[k]))
        if prof.get("city_name"):
            bits.append(f"{str(prof['city_name']).title()}, {prof.get('state_code', '')}")
        if not prof.get("active", True):
            bits.append("INACTIVE")
    vw = viol[viol["start"] >= start] if not viol.empty and "start" in viol else viol
    if not vw.empty:
        bits.append(f"{len(vw)} violations in window ({int(vw['health_based'].sum())} health-based, "
                    f"{int((vw['status'] == 'Open').sum())} open)")
    st.write(" · ".join(bits))

    is_tn = pwsid[:2] == "TN"
    has_state = dww.supported(pwsid[:2]) or is_tn
    sections = ["Violations", "Lead & copper",
                "Sample results (state)" if has_state else "Sample results",
                "PFAS (UCMR 5)", "Standards", "Download"]
    section = st.segmented_control("DW section", sections, default="Violations",
                                   key="dw_section", label_visibility="collapsed") or "Violations"

    # ---- violations
    if section == "Violations":
        if vw.empty:
            st.info("No federal violations recorded in this window.")
        else:
            fig = DV.violations_timeline(vw, x_range)
            if fig is not None:
                st.plotly_chart(fig, config=PLOT_CFG, theme="streamlit", width="stretch")
            st.caption("One row per contaminant or rule, most frequent at top. Red diamond: "
                       "health-based (MCL, MRDL, treatment technique). Grey: monitoring, reporting "
                       "and other. The faint bar is the compliance period. Hover for status and, "
                       "for MCL violations, the measured value vs the MCL.")
            st.dataframe(DV.violation_summary(vw), hide_index=True, width="stretch")

    # ---- lead & copper
    if section == "Lead & copper":
        lcw = lc[lc["date"] >= start] if not lc.empty else lc
        if lcw.empty:
            st.info("No lead and copper 90th percentile results in EPA's data for this window.")
        else:
            c1, c2 = st.columns(2)
            for col, a in [(c1, "Lead"), (c2, "Copper")]:
                fig = DV.lead_copper_panel(lcw, a, x_range)
                with col:
                    if fig is None:
                        st.caption(f"No {a.lower()} results.")
                    else:
                        st.plotly_chart(fig, config=PLOT_CFG, theme="streamlit", width="stretch")
            st.caption("90th percentile of tap samples per monitoring period vs the federal action "
                       "level (40 CFR 141.80). An action level exceedance triggers treatment, "
                       "public education and lead service line steps; it is not itself an MCL "
                       "violation. The Lead and Copper Rule Improvements change these thresholds "
                       "over the next few years; values shown are the current federal action levels.")

    # ---- state sample results
    if section.startswith("Sample results"):
        if not has_state:
            why = dww.OTHER_PLATFORM.get(pwsid[:2], "a different platform")
            st.info(f"{pwsid[:2]} sample results aren't connected yet: {why}. Federal violations, "
                    "lead and copper, and PFAS are still available in the other sections.")
        else:
            c1, c2, _ = st.columns([2, 2, 4])
            s_years = c1.selectbox("Sample history", [1, 2, 3, 5, 10], index=3,
                                   format_func=lambda y: f"last {y} year{'s' if y > 1 else ''}",
                                   key="dw_s_years")
            cap = 0 if is_tn else c2.selectbox(
                "Max samples", [200, 400, 800, 1500], index=1, key="dw_cap",
                help="Each sample is its own page on the state site. Bigger "
                     "numbers take longer the first time; results are cached after.")
            if st.button("Load state sample results", type="primary", key="dw_load_state"):
                st.session_state["dw_state_req"] = (pwsid, s_years, cap, prof.get("pws_name") or "")
            req = st.session_state.get("dw_state_req")
            if req and req[0] == pwsid:
                try:
                    res, info = get_state(*req)
                except Exception as e:  # noqa: BLE001
                    note = dww.ACCESS_NOTES.get(pwsid[:2])
                    if is_tn:
                        st.error("Tennessee's dashboard didn't return this system. It matches on the "
                                 "exact system name, so a name that differs from EPA's can miss; "
                                 "TDEC also only publishes what it publishes (Chem/Rad samples, "
                                 f"roughly the last few years). Detail: {str(e)[:200]}")
                    else:
                        st.error((f"{note}, so sample results can't be pulled from here. "
                                  "Federal data in the other sections still works.") if note else
                                 f"The {pwsid[:2]} state site didn't cooperate: {str(e)[:250]}")
                    res, info = pd.DataFrame(), {}
                if info.get("tn"):
                    first = info.get("first")
                    st.caption(f"{info['rows_kept']} of {info['rows_found']} results kept for the "
                               f"window, from {info['base']}"
                               + (f". TN's record for this system starts {first:%b %Y}."
                                  if pd.notna(first) else "."))
                elif info:
                    msg = (f"{info['samples_fetched']} of {info['samples_found']} samples read "
                           f"from {info.get('base', '')}.")
                    if info.get("truncated"):
                        msg += " Hit the max-samples cap; most recent samples shown."
                    if info.get("failed"):
                        msg += f" {info['failed']} sample pages failed to load."
                    st.caption(msg)
                if not res.empty:
                    d = DV.prepare_results(res)
                    panels = DV.analyte_panels(d)
                    st.caption("One chart per analyte that has a federal standard, highest "
                               "% of standard first. Blue: detected result. Hollow grey: non-detect "
                               "at its reporting level. Bold blue line: running annual average "
                               "(highest location) where the standard is averaged. Orange: federal "
                               "standard. Red diamond: a single result above it.")
                    rows = [panels.iloc[i:i + 3] for i in range(0, len(panels), 3)]
                    k = 0
                    for chunk in rows:
                        for col, (_, r) in zip(st.columns(3), chunk.iterrows()):
                            k += 1
                            with col:
                                st.markdown(f"**{_nice(r['analyte'])}**  \n"
                                            f"{r['std_kind']} {V._fmt(r['std_value'], r['std_unit'])}"
                                            f" · {DV.BASIS_TEXT.get(r['std_basis'], '')}")
                                st.plotly_chart(DV.analyte_panel(d, r, x_range), config=PLOT_CFG,
                                                theme="streamlit", width="stretch", key=f"dwa{k}")
                                mp = r["max_pct"]
                                st.caption(f"{r['samples']} results, {r['detects']} detected"
                                           + (f" · max {mp:.0%} of standard" if pd.notna(mp) else ""))
                    other = DV.other_analytes(d)
                    if not other.empty:
                        with st.expander(f"{len(other)} other analytes reported (no federal MCL in "
                                         "this tool's table)"):
                            st.dataframe(other, hide_index=True, width="stretch")
                    st.session_state["dw_state_results"] = d

    # ---- PFAS
    if section == "PFAS (UCMR 5)":
        if not ucmr.available():
            st.info("UCMR data isn't loaded yet. Run `python scripts/prep_ucmr.py <zip>` once and "
                    "commit data_static/ucmr5_slim.parquet (see the README).")
        else:
            u = get_ucmr(pwsid)
            if u.empty:
                st.info("This system isn't in UCMR 5. Systems serving over 10,000 people, plus a "
                        "representative sample of smaller ones, were required to monitor.")
            else:
                ux = [u["date"].min() - pd.Timedelta(days=30), u["date"].max() + pd.Timedelta(days=30)]
                grid = st.columns(3)
                i = 0
                for std in S.PFAS:
                    fig = DV.pfas_panel(u, std, ux)
                    if fig is None:
                        continue
                    with grid[i % 3]:
                        st.markdown(f"**{std.key.upper().replace('_', '-')}**  \n"
                                    f"MCL {std.value:g} ng/L")
                        st.plotly_chart(fig, config=PLOT_CFG, theme="streamlit", width="stretch",
                                        key=f"pfas{std.key}")
                        st.caption(std.status)
                    i += 1
                hi = ucmr.hazard_index(u)
                fig = DV.hi_panel(hi, ux)
                if fig is not None:
                    with grid[i % 3]:
                        st.markdown("**Hazard index** (PFHxS, PFNA, HFPO-DA, PFBS)  \nMCL 1")
                        st.plotly_chart(fig, config=PLOT_CFG, theme="streamlit", width="stretch",
                                        key="pfas_hi")
                        st.caption(S.HI_STATUS)
                st.caption("UCMR 5 results (2023 to 2025) per sample. PFAS MCLs are compliance "
                           "standards on a running annual average from compliance monitoring; "
                           "UCMR results are a screen of where a system stands against them.")
                st.dataframe(DV.ucmr_table(u), hide_index=True, width="stretch")

    # ---- standards reference
    if section == "Standards":
        st.dataframe(S.table(), hide_index=True, width="stretch")
        st.caption("Federal values from 40 CFR Part 141. PFAS values from the April 2024 rule, "
                   "with the status of EPA's May 2026 proposals noted.")

    # ---- download
    if section == "Download":
        for label, df, fn in [("Violations", viol, "violations"), ("Lead & copper", lc, "lead_copper"),
                              ("State sample results", st.session_state.get("dw_state_results"),
                               "state_samples"),
                              ("UCMR 5", get_ucmr(pwsid) if ucmr.available() else None, "ucmr5")]:
            if df is not None and len(df):
                st.download_button(f"Download {label} CSV", df.to_csv(index=False).encode("utf-8"),
                                   file_name=f"{pwsid}_{fn}.csv", mime="text/csv", key=f"dl{fn}")
            else:
                st.caption(f"{label}: nothing loaded" + (" (open Sample results first)"
                                                         if fn == "state_samples" else ""))
