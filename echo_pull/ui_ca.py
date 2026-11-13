"""California mode: WDRs, MS4s and other Water Board orders, eSMR monitoring, industrial stormwater."""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from .ca import viz_ca as CV
from .ca import waterboards as W

PLOT_CFG = {"displaylogo": False, "modeBarButtonsToRemove": ["lasso2d", "select2d"]}
TTL = 7 * 24 * 3600


@st.cache_data(ttl=TTL, show_spinner=False, max_entries=200)
def find(text: str):
    return W.find_facilities(text), W.industrial_facilities(text)


@st.cache_data(ttl=TTL, show_spinner=False, max_entries=200)
def orders(place_id: str):
    return W.facility_orders(place_id)


@st.cache_data(ttl=TTL, show_spinner=False, max_entries=100)
def esmr(place_id: str, years: int):
    return W.esmr(place_id, years)


@st.cache_data(ttl=TTL, show_spinner=False, max_entries=200)
def sw_sites(text: str):
    return W.industrial_facilities(text)


@st.cache_data(ttl=TTL, show_spinner=False, max_entries=100)
def sw_monitoring(wdid: str):
    return W.industrial_monitoring(wdid)


def _grid(items, draw, n=3):
    for i in range(0, len(items), n):
        cols = st.columns(n)
        for c, it in zip(cols, items[i:i + n]):
            with c:
                draw(it)


ORDER_SHOW = {"program_category": "Program", "reg_measure_type": "Type", "order_number": "Order",
              "wdid": "WDID", "npdes_number_ca_number": "NPDES", "status": "Status",
              "effective_date": "Effective", "expiration_review_date": "Expires / review",
              "design_flow": "Design MGD", "threat_to_water_quality": "Threat",
              "complexity": "Complexity", "facility_waste_type": "Waste type",
              "reclamation": "Reclamation", "individual_general": "Indiv/General"}


# ------------------------------------------------------------------ page
def render():
    st.caption("California Water Boards data for sites ECHO misses or only partly covers: WDRs "
               "(land and groundwater discharge), MS4s and other state orders, electronic "
               "self-monitoring (eSMR) results, and industrial stormwater sampling vs the "
               "permit's action levels. Public data; nothing you enter is stored.")
    with st.form("ca_find"):
        c1, c2, _ = st.columns([3, 1, 4])
        text = c1.text_input("WDID, order number, CA NPDES number, or facility name",
                             placeholder="5B24NC00303  ·  R5-2013-0021  ·  Planada",
                             key="ca_text").strip()
        years = c2.number_input("Years", min_value=1, max_value=20, value=10, step=1, key="ca_years")
        go = st.form_submit_button("Look up", type="primary")
    if go:
        if len(text) < 3:
            st.error("Type at least three characters.")
            st.stop()
        with st.spinner("Searching the Water Boards' orders and stormwater records..."):
            try:
                fac, sw = find(text)
            except W.CAError as e:
                st.error(f"data.ca.gov didn't answer: {e}. Try again in a minute.")
                st.stop()
        st.session_state["ca"] = {"text": text, "years": int(years), "fac": fac, "sw": sw}
        st.session_state.pop("ca_pick", None)

    P = st.session_state.get("ca")
    if not P:
        st.stop()
    fac, sw = P["fac"], P["sw"]
    if fac.empty and sw.empty:
        st.warning("Nothing in the Water Boards' orders or industrial stormwater records matches "
                   "that. For a WDID, type it exactly; for a name, fewer words usually helps.")
        st.stop()

    # ---- pick a site when the search is ambiguous
    choices = [(f"{r.facility_name} · {W._s(r.city)} · {W._s(r.programs)} · place {r.facility_id}",
                ("ciwqs", r.facility_id)) for r in fac.itertuples()]
    choices += [(f"{r.FACILITY_NAME} · {W._s(r.FACILITY_CITY)} · industrial stormwater "
                 f"{r.WDID} ({r.STATUS})", ("sw", r.WDID)) for r in sw.itertuples()]
    if len(choices) > 1:
        labels = [c[0] for c in choices]
        pick = st.selectbox(f"{len(choices)} matches", labels, key="ca_pick")
        kind, ident = dict(choices)[pick]
    else:
        kind, ident = choices[0][1]

    if kind == "ciwqs":
        show_facility(str(ident), P["years"])
    else:
        show_stormwater_only(ident, sw, P["years"])


def show_facility(place_id: str, years: int):
    with st.spinner("Reading the facility's orders..."):
        o = orders(place_id)
    if o.empty:
        st.error("No orders found for that facility.")
        return
    f = o.iloc[0]
    st.subheader(f"{f['facility_name']}" + (f", {f['place_city']}" if pd.notna(f.get("place_city")) else ""))
    st.write(" · ".join(str(x) for x in [f.get("place_type"), f.get("place_address"),
                                         f"{f.get('place_county')} County" if pd.notna(f.get("place_county")) else None,
                                         f"Region {f.get('facility_region')}", f"place ID {place_id}"]
                        if x is not None and pd.notna(x)))
    act = o[o["active"]]
    for _, r in act.iterrows():
        st.markdown(f"**Active:** {W.order_summary(r)}")
    if act.empty:
        st.caption("No active orders on record.")
    st.markdown(f"[Open this facility in CIWQS]({W.ciwqs_url(place_id)}): related parties (owner, "
                "onsite manager, data submitters), every order, violations, enforcement and "
                "inspections. Order PDFs, including WDR limits, are on the Regional Board's site.")
    has_npdes = act["program_category"].eq("NPDESWW").any()
    if has_npdes:
        npdes = act["npdes_number_ca_number"].dropna()
        st.info("This site has an active NPDES permit"
                + (f" ({npdes.iloc[0]})" if len(npdes) else "")
                + ", so its DMRs are in the Wastewater (NPDES) mode, with limits.")

    sec = st.segmented_control("Section", ["Monitoring (eSMR)", "Industrial stormwater", "Orders",
                                           "Download"], default="Monitoring (eSMR)",
                               key="ca_section", label_visibility="collapsed") or "Monitoring (eSMR)"
    x_range = [pd.Timestamp(year=date.today().year - years, month=1, day=1),
               pd.Timestamp(date.today()) + pd.Timedelta(days=20)]

    if sec == "Orders":
        t = o[[c for c in ORDER_SHOW if c in o]].rename(columns=ORDER_SHOW)
        for c in ("Effective", "Expires / review"):
            t[c] = pd.to_datetime(t[c]).dt.date
        st.dataframe(t, hide_index=True, width="stretch")
        st.caption("Threat (1-3) and complexity (A-C) are the Water Board's ratings that set the "
                   "annual fee; 1A is the highest. 'Expires / review' for a WDR is the review date: "
                   "WDRs don't expire, but past-due reviews often mean a rewrite is coming.")
        return

    if sec == "Industrial stormwater":
        q = str(f["facility_name"])
        with st.spinner("Looking for this site's industrial stormwater coverage..."):
            cand = sw_sites(q.split(" - ")[0][:40])
            near = W.near_matches(f, sw_sites(str(f.get("place_city") or q)))
        found = pd.concat([near, cand]).drop_duplicates("WDID") if not near.empty or not cand.empty \
            else pd.DataFrame()
        if found.empty:
            st.info("No industrial stormwater coverage found under this name or address. Treatment "
                    "plants of 1 MGD or more usually need it; it may be filed under the agency's "
                    "name. Search the agency or a stormwater WDID in the box above.")
            return
        labels = [f"{r.FACILITY_NAME} · {r.FACILITY_ADDRESS} · {r.WDID} ({r.STATUS})"
                  for r in found.itertuples()]
        pick = st.selectbox("Stormwater coverage", labels, key="ca_sw_pick")
        stormwater(found.iloc[labels.index(pick)], x_range)
        return

    if sec == "Download":
        d, _ = esmr(place_id, years)
        st.download_button("Orders (CSV)", o.to_csv(index=False).encode(), f"{place_id}_orders.csv")
        if not d.empty:
            st.download_button("eSMR results (CSV)", d.to_csv(index=False).encode(),
                               f"{place_id}_esmr.csv")
        return

    # ---- monitoring (eSMR)
    with st.spinner("Reading eSMR monitoring results (the newest year comes live from the "
                    "state and can take up to a minute)..."):
        try:
            d, info = esmr(place_id, years)
        except W.CAError as e:
            st.error(f"data.ca.gov didn't answer: {e}")
            return
    if d.empty:
        st.info("This site doesn't file electronic monitoring data (eSMR). Most WDR dischargers "
                "upload their monitoring reports as PDFs, which are in CIWQS through the link above.")
        return
    panels = W.esmr_panels(d)
    st.caption(f"{len(d):,} results, {d['date'].min():%b %Y} to {d['date'].max():%b %Y}. Blue: "
               "effluent. Grey: influent. Hollow: non-detect at its reporting level. Line: effluent "
               "monthly median, broken where months are missing. Where influent runs 8x+ above "
               "effluent the axis is log; a few wild entries (likely typos) sit as triangles on "
               "the top edge, true value on hover. eSMR carries results only, so there are no "
               "limit lines; WDR limits are in the order.")

    def draw(r):
        st.markdown(f"**{r['parameter']}**")
        st.plotly_chart(CV.esmr_panel(d, r, x_range), config=PLOT_CFG, width="stretch",
                        key=f"esmr_{r['parameter']}_{r['units']}")
        st.caption(CV.esmr_caption(d, r))
    _grid([r for _, r in panels.iterrows()], draw)


def show_stormwater_only(wdid: str, sw: pd.DataFrame, years: int):
    r = sw[sw["WDID"] == wdid].iloc[0]
    st.subheader(f"{r['FACILITY_NAME']}, {W._s(r.get('FACILITY_CITY'))}")
    x_range = [pd.Timestamp(year=date.today().year - years, month=1, day=1),
               pd.Timestamp(date.today()) + pd.Timedelta(days=20)]
    stormwater(r, x_range)


def stormwater(r: pd.Series, x_range):
    def g(k):
        v = r.get(k)
        return None if v is None or pd.isna(v) else v
    bits = [f"Industrial stormwater WDID {r['WDID']}", f"status {str(g('STATUS') or '').lower()}",
            f"coverage since {pd.to_datetime(g('NOI_PROCESSED_DATE')):%b %Y}" if g("NOI_PROCESSED_DATE") else None,
            f"SIC {g('PRIMARY_SIC')}" if g("PRIMARY_SIC") else None,
            f"{g('FACILITY_TOTAL_SIZE')} {g('FACILITY_TOTAL_SIZE_UNIT') or ''} site" if g("FACILITY_TOTAL_SIZE") else None,
            f"{g('PERCENT_OF_SITE_IMPERVIOUSNESS')}% impervious" if g("PERCENT_OF_SITE_IMPERVIOUSNESS") else None,
            f"receiving water: {g('RECEIVING_WATER_NAME')}" if g("RECEIVING_WATER_NAME") else None]
    st.write(" · ".join(b for b in bits if b))
    who = [f"Permittee: {g('OPERATOR_NAME')}" if g("OPERATOR_NAME") else None,
           "Contact: " + " ".join(x for x in (g("FACILITY_CONTACT_FIRST_NAME"),
                                              g("FACILITY_CONTACT_LAST_NAME")) if x)
           if g("FACILITY_CONTACT_LAST_NAME") else None, g("FACILITY_PHONE"), g("FACILITY_EMAIL")]
    st.caption(" · ".join(w for w in who if w) + "  \nSMARTS calls the permittee the 'operator': "
               "it's the party legally responsible for the permit, not a contract or certified "
               f"operator. Reports and SWPPP: [SMARTS public search]({W.SMARTS_PUBLIC}) "
               "(Public User Menu, then search by WDID).")
    with st.spinner("Reading stormwater sampling results..."):
        try:
            m = sw_monitoring(str(r["WDID"]))
        except W.CAError as e:
            st.error(f"data.ca.gov didn't answer: {e}")
            return
    m = m[m["date"] >= x_range[0]] if not m.empty else m
    if m.empty:
        st.info("No sampling results on file for this site in the window.")
        return
    keys = CV.sw_panels(m)
    st.caption("Numeric action levels (NALs) from the Industrial General Permit (Order "
               "2014-0057-DWQ as amended). The annual NAL applies to each reporting year's average "
               "(thick segments; red when over); the instantaneous NAL (dotted) to single TSS and "
               "O&G results; pH to the 6.0-9.0 range. NALs trigger corrective-action levels, not "
               "violations. Hollow: non-detect at its reporting level.")
    if not keys:
        st.info("This site's results don't include any parameter with a NAL.")
    def draw(k):
        st.markdown(f"**{CV.NAL_NAME.get(k, k.title())}**")
        fig = CV.sw_panel(m, k, x_range)
        if fig is not None:
            st.plotly_chart(fig, config=PLOT_CFG, width="stretch", key=f"sw_{r['WDID']}_{k}")
            st.caption(CV.sw_caption(m, k))
    _grid(keys, draw)
    other = m[m["nal_key"].isna()]
    if not other.empty:
        with st.expander(f"{other['PARAMETER'].nunique()} other parameters (no NAL)"):
            st.dataframe(other.groupby(["PARAMETER", "UNITS"], dropna=False)
                         .agg(results=("result", "size"), max=("result", "max"),
                              last=("date", "max")).reset_index(), hide_index=True, width="stretch")
    st.download_button("Stormwater results (CSV)", m.to_csv(index=False).encode(),
                       f"{r['WDID'].replace(' ', '')}_stormwater.csv")
