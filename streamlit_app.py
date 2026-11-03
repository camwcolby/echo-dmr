"""ECHO DMR download page.

Type an NPDES permit ID, get up to 10 years of DMR data (limits, reported values, violations)
from EPA ECHO as a CSV, in the same raw format ECHO's own "Download Data" button gives.

Run locally:   streamlit run streamlit_app.py
"""
from __future__ import annotations

import re
import time
from datetime import date

import pandas as pd
import streamlit as st

from echo_pull import client

st.set_page_config(page_title="ECHO DMR download", layout="centered")

NPDES_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{7}$")   # e.g. TN0020117, CA0107409, MAG580001


@st.cache_data(ttl=7 * 24 * 3600, show_spinner=False, max_entries=200)
def fetch_year(npdes_id: str, year: int, _cache_day: str) -> pd.DataFrame:
    # _cache_day keeps the current (partial) year from being cached past today
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
        return None   # a name is nice to have; never block the download on it


st.title("ECHO DMR download")
st.caption("Pulls Discharge Monitoring Report data for one NPDES permit from EPA ECHO. "
           "Public data; nothing you enter is stored.")

with st.form("pull"):
    c1, c2 = st.columns([3, 1])
    npdes = c1.text_input("NPDES permit ID", placeholder="TN0020117").strip().upper()
    years = c2.number_input("Years", min_value=1, max_value=15, value=10, step=1)
    go = st.form_submit_button("Get data", type="primary")

if go:
    if not NPDES_RE.match(npdes):
        st.error("That doesn't look like an NPDES ID: two letters then seven letters/digits, "
                 "like TN0020117. Look one up at https://echo.epa.gov/facilities/facility-search")
        st.stop()

    name = facility_name(npdes)
    if name:
        st.write(f"**{npdes}**: {name}")

    today = date.today()
    start = pd.Timestamp(today) - pd.DateOffset(years=int(years))   # rolling window, not calendar yrs
    yrs = list(range(start.year, today.year + 1))
    bar = st.progress(0.0, text="Starting...")
    frames, failed = [], []
    for i, y in enumerate(yrs):
        bar.progress(i / len(yrs), text=f"Pulling {y}...")
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
        msg = "ECHO returned no DMR data for that permit."
        if failed:
            msg += f" EPA's service also failed for {', '.join(map(str, failed))}; try again shortly."
        st.error(msg + " Check the ID at https://echo.epa.gov/effluent-charts")
        st.stop()

    out = pd.concat(frames, ignore_index=True).drop_duplicates()
    out.columns = [c.strip().lower() for c in out.columns]
    if "monitoring_period_end_date" in out.columns:
        keep = pd.to_datetime(out["monitoring_period_end_date"], errors="coerce") > start
        out = out[keep | keep.isna()]
    if "monitoring_period_end_date" in out.columns:
        mpe = pd.to_datetime(out["monitoring_period_end_date"], errors="coerce")
        first, last = mpe.min(), mpe.max()
        months = mpe.dt.to_period("M").nunique()
        span = f"{first:%b %Y} to {last:%b %Y}" if pd.notna(first) else "unknown dates"
    else:
        mpe, span, months = None, "unknown dates", 0

    st.success(f"{len(out):,} rows, {span} ({months} months with data).")
    if failed:
        st.warning(f"EPA's service failed for {', '.join(map(str, failed))}, so those years are "
                   "missing from the file. Run it again to fill them in.")
    have = sorted(pd.to_datetime(out.get("monitoring_period_end_date"),
                                 errors="coerce").dt.year.dropna().unique().astype(int)) \
        if mpe is not None else []
    gaps = [y for y in yrs[1:] if y not in have and y not in failed]
    if gaps:
        st.info(f"No DMR data in ECHO for {', '.join(map(str, gaps))}. That usually means "
                "the permit's history in ECHO starts later, not an error.")

    st.download_button(
        "Download CSV", out.to_csv(index=False).encode("utf-8"),
        file_name=f"echo_dmr_{npdes}_{start:%Y%m}_{today:%Y%m}.csv", mime="text/csv", type="primary")

    with st.expander("What's in the file"):
        st.markdown(
            "One row per reported value: outfall, monitoring location, parameter, statistical "
            "base, limit, reported value, units, NODI codes and violations. Columns are exactly "
            "as ECHO provides them. Use `dmr_value_standard_units` with `standard_unit_desc` "
            "(and `limit_value_standard_units`) so values and limits are in the same units.")
        if "parameter_desc" in out.columns:
            st.dataframe(out["parameter_desc"].value_counts().rename("rows"),
                         width="stretch")
