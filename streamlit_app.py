"""ECHO explorer: wastewater (NPDES DMRs) and drinking water (PWSID).

Run locally:   python -m streamlit run streamlit_app.py
"""
import streamlit as st

st.set_page_config(page_title="ECHO DMR explorer", layout="wide")

from echo_pull import ui_dw, ui_ww  # noqa: E402

st.title("ECHO DMR explorer")
mode = st.segmented_control("Mode", ["Wastewater (NPDES)", "Drinking water (PWSID)"],
                            default="Wastewater (NPDES)", key="mode",
                            label_visibility="collapsed") or "Wastewater (NPDES)"
if mode.startswith("Wastewater"):
    ui_ww.render()
else:
    ui_dw.render()
