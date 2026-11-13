"""California Water Boards: lookup parsing, eSMR tidying, IGP NAL logic, charts."""
import pandas as pd
import pytest

from echo_pull.ca import viz_ca as CV
from echo_pull.ca import waterboards as W


@pytest.mark.parametrize("text,kind", [
    ("5B24NC00303", "wdid"), ("R5-2013-0021", "order"), ("2014-0057-DWQ", "order"),
    ("CA0023639", "npdes"), ("5S34I012345", "sw_wdid"), ("1 08I004046", "sw_wdid"),
    ("Planada", "name"), ("Mountain House", "name")])
def test_kind_of(text, kind):
    assert W.kind_of(text) == kind


def test_nal_matching_and_values():
    assert W.nal_for("Total Suspended Solids (TSS)")[0] == "tss"
    assert W.nal_for("Oil and Grease")[0] == "og"
    assert W.nal_for("pH")[0] == "ph"
    assert W.nal_for("Total Phosphorus")[0] == "tp" and W.nal_for("Phosphorus, Total")[0] == "tp"
    assert W.nal_for("Zinc, Dissolved") == (None, None)          # NALs are total metals
    assert W.IGP_NAL["tss"]["annual"] == 100 and W.IGP_NAL["tss"]["inst"] == 400
    assert W.IGP_NAL["og"]["inst"] == 25 and "inst" not in W.IGP_NAL["zinc"]


def _sw():
    rows = []
    for yr, vals in [(2023, [50, 90, 160]), (2024, [120, 450, 30])]:
        for i, v in enumerate(vals):
            rows.append({"PARAMETER": "Total Suspended Solids (TSS)", "UNITS": "mg/L",
                         "RESULT": v, "RESULT_QUALIFIER": "=", "RL": None,
                         "SAMPLE_DATE": f"{yr}-0{i + 1}-15", "REPORTING_YEAR": yr,
                         "MONITORING_LOCATION_NAME": "SW-1"})
    rows.append({"PARAMETER": "Zinc, Total", "UNITS": "ug/L", "RESULT": 300,
                 "RESULT_QUALIFIER": "=", "RL": None, "SAMPLE_DATE": "2024-02-01",
                 "REPORTING_YEAR": 2024, "MONITORING_LOCATION_NAME": "SW-1"})
    rows.append({"PARAMETER": "pH", "UNITS": "SU", "RESULT": 9.4, "RESULT_QUALIFIER": "=",
                 "RL": None, "SAMPLE_DATE": "2024-03-01", "REPORTING_YEAR": 2024,
                 "MONITORING_LOCATION_NAME": "SW-1"})
    d = pd.DataFrame(rows)
    d["date"] = pd.to_datetime(d["SAMPLE_DATE"])
    d["result"] = d["RESULT"].astype(float)
    d["rl"] = pd.to_numeric(d["RL"])
    d["nd"] = False
    d["plot_value"] = d["result"]
    d["nal_key"] = d["PARAMETER"].map(lambda p: W.nal_for(p)[0])
    d["to_mgl"] = d["UNITS"].str.lower().map(W._UNIT_TO_MGL)
    return d


def test_nal_table_annual_average_and_instantaneous():
    t = W.nal_table(_sw()).set_index(["nal_key", "year"])
    assert t.loc[("tss", 2023), "average"] == pytest.approx(100)          # at, not over
    assert not t.loc[("tss", 2023), "over_annual"]
    assert t.loc[("tss", 2024), "over_annual"] and t.loc[("tss", 2024), "inst_hits"] == 1
    assert t.loc[("zinc", 2024), "average"] == pytest.approx(0.3)          # 300 ug/L -> mg/L
    assert t.loc[("zinc", 2024), "over_annual"]
    assert t.loc[("ph", 2024), "inst_hits"] == 1


def test_stormwater_panels_order_and_draw():
    m = _sw()
    keys = CV.sw_panels(m)
    assert keys[:2] == ["tss", "ph"] and "zinc" in keys
    for k in keys:
        assert CV.sw_panel(m, k) is not None
    assert "over the NAL in 1 year (2024)" in CV.sw_caption(m, "tss")


def test_esmr_tidy_nondetects_and_calculated():
    raw = pd.DataFrame({
        "location": ["EFF-001", "EFF-001", "INF-001", "EFF-001"],
        "location_place_type": ["Effluent Monitoring", "Effluent Monitoring",
                                "Influent Monitoring", "Effluent Monitoring"],
        "parameter": ["Ammonia, Total (as N)"] * 3 + ["Ammonia, Total (as N)"],
        "calculated_method": ["NA", "NA", "NA", "Average Monthly (AMEL)"],
        "qualifier": ["=", "ND", "=", "="], "result": [1.2, "NaN", 30, 1.1],
        "units": ["mg/L"] * 4, "mdl": ["NaN"] * 4, "ml": ["NaN"] * 4, "rl": ["NaN", 0.1, "NaN", "NaN"],
        "sampling_date": ["2025-01-05", "2025-02-05", "2025-01-05", "2025-01-31"],
        "report_name": ["x"] * 4})
    d = W.tidy_esmr(raw)
    assert d["nd"].tolist() == [False, True, False, False]
    assert d.loc[1, "plot_value"] == pytest.approx(0.1)                    # ND at its RL
    assert d["calculated"].tolist() == [False, False, False, True]
    assert set(d["stream"]) == {"Effluent", "Influent"}
    p = W.esmr_panels(d)
    assert p.iloc[0]["results"] == 3                                       # calculated row left out
    assert CV.esmr_panel(d, p.iloc[0]) is not None
    assert "influent median 30 mg/L" in CV.esmr_caption(d, p.iloc[0])


def test_order_summary_and_link():
    o = pd.Series({"program_category": "WDR", "order_number": "R5-2013-0021",
                   "wdid": "5B24NC00303", "npdes_number_ca_number": pd.NA,
                   "effective_date": pd.Timestamp("2013-04-11"),
                   "expiration_review_date": pd.Timestamp("2023-04-11"), "design_flow": 0.58,
                   "threat_to_water_quality": "2", "complexity": "B",
                   "wdr_review_revise_renew": pd.NA})
    s = W.order_summary(o)
    assert s.startswith("Waste discharge requirements · order R5-2013-0021 · WDID 5B24NC00303")
    assert "expired / review was due Apr 2023" in s and "threat/complexity 2B" in s
    assert W.ciwqs_url(273182).endswith("reportName=facilityAtAGlance&placeID=273182")


def test_same_address_stormwater_match():
    fac = pd.Series({"place_address": "8597 East Toews"})
    sw = pd.DataFrame({"FACILITY_ADDRESS": ["8597 East Toews Ave", "40 North Market St", None],
                       "WDID": ["A", "B", "C"]})
    assert W.near_matches(fac, sw)["WDID"].tolist() == ["A"]


def _stream_df(inf, eff):
    rows = [dict(stream="Influent", plot_value=v, nd=False) for v in inf] + \
           [dict(stream="Effluent", plot_value=v, nd=False) for v in eff]
    return pd.DataFrame(rows)


def test_axis_log_when_influent_dwarfs_effluent():
    s = CV._scale(_stream_df([250, 300, 280, 0], [10, 12, 15, 11]))
    assert s["log"] and s["cap"] is None


def test_axis_capped_for_entry_errors_not_hidden():
    eff = [0.3] * 200 + [291]                       # one 300 MGD slip at a 0.3 MGD plant
    s = CV._scale(_stream_df([0.33] * 200, eff))
    assert not s["log"] and s["cap"] < 1 and s["n_off"] == 1 and s["max"] == 291
