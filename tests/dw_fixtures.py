"""Fake DWW pages and EPA responses, shaped like the real ones (column headers and link patterns
copied from live MD, SC, AK and CA Drinking Water Watch pages, Nov 2026)."""
import numpy as np
import pandas as pd

BASE = "https://dww.example.gov/DWW"


def _layout(inner: str) -> str:
    # real DWW pages wrap data tables in layout tables; the parser must not grab the outer one
    return (f"<html><body><table><tr><td><table><tr><td>Water System No.:</td><td>XX0000001</td>"
            f"</tr></table></td></tr><tr><td>{inner}</td></tr></table></body></html>")


SEARCH = _layout(
    "<table><tr><th>Water System No.</th><th>Name</th><th>Type</th><th>Status</th></tr>"
    "<tr><td><a href='WaterSystemDetail.jsp?tinwsys_is_number=1234&tinwsys_st_code=XX"
    "&wsnumber=XX0000001'>XX0000001</a></td><td>TESTVILLE WATER</td><td>C</td><td>A</td></tr>"
    "</table>")


def samples_page(n=10):
    rows = []
    for i in range(n):          # both locations every quarter, like a Stage 2 DBP schedule
        d = pd.Timestamp("2025-12-15") - pd.DateOffset(months=3 * (i // 2))
        rows.append(
            f"<tr><td><a href='NonTcrSampleResults.jsp?sample_number=S{i}&collection_date="
            f"{d:%m-%d-%Y}&tinwsys_is_number=1234&tinwsys_st_code=XX&tsasampl_is_number={900+i}"
            f"&tsasampl_st_code=XX&history=1&counter=0'>S{i}</a></td><td>RT</td>"
            f"<td>{d:%m-%d-%Y} 10:00:00</td><td>{'DBP101' if i % 2 else 'DBP102'}</td>"
            f"<td>SITE {i % 2}</td><td>TEST LAB</td><td></td></tr>")
    return _layout(
        "<table><tr><th>Lab Sample No.</th><th>Type</th><th>Collection Date &amp; Time</th>"
        "<th>Sampling Point</th><th>Sample Location</th><th>Laboratory</th><th>Print</th></tr>"
        + "".join(rows) + "</table>")


def result_page(i):
    tthm = 0.050 + 0.012 * (i % 3)          # mg/L, some quarters near the 0.080 MCL
    nitrate_lt = "<" if i % 2 else ""
    return _layout(
        "<table><tr><th>Analyte Code</th><th>Analyte Name</th><th>Method Code</th>"
        "<th>Less than Indicator</th><th>Level Type</th><th>Reporting Level</th>"
        "<th>Concentration level</th><th>Monitoring Period Begin Date</th>"
        "<th>Monitoring Period End Date</th></tr>"
        f"<tr><td>2950</td><td>TTHM</td><td>524.2</td><td></td><td>MRL</td><td>0.5 UG/L</td>"
        f"<td>{tthm * 1000:.1f} UG/L</td><td>01-01-2025</td><td>03-31-2025</td></tr>"
        f"<tr><td>1040</td><td>NITRATE</td><td>300.0</td><td>{nitrate_lt}</td><td>MRL</td>"
        f"<td>0.1 MG/L</td><td>{'0.1' if nitrate_lt else '12.5' if i == 0 else '3.2'} MG/L</td>"
        "<td>01-01-2025</td><td>12-31-2025</td></tr>"
        "<tr><td>1915</td><td>HARDNESS, TOTAL (AS CACO3)</td><td>2340B</td><td></td><td>MRL</td>"
        "<td>1 MG/L</td><td>140 MG/L</td><td>01-01-2025</td><td>12-31-2025</td></tr>"
        "</table>")


def fake_get(sess, url, timeout=45, retries=2):
    if "index.jsp" in url:
        return "<html>ok</html>"
    if "WaterSystems.jsp" in url:
        return SEARCH
    if "NonTcrSampleResults.jsp" in url:
        i = int(url.split("tsasampl_is_number=")[1].split("&")[0]) - 900
        return result_page(i)
    if "NonTcrSamples.jsp" in url:
        return samples_page()
    raise AssertionError(url)


def fake_query(table, filters):
    if table == "water_system":
        return pd.DataFrame([{"pwsid": "XX0000001", "pws_name": "TESTVILLE WATER",
                              "pws_type_code": "CWS", "primary_source_code": "SW",
                              "population_served_count": "24500", "pws_activity_code": "A",
                              "city_name": "TESTVILLE", "state_code": "XX"}])
    if table == "violation":
        rows = []
        for i, (cat, hb, code, st) in enumerate([("MCL", "Y", "2950", "R"), ("MR", "N", "5000", "R"),
                                                 ("MR", "N", "3100", "O"), ("TT", "Y", "0300", "O")]):
            b = pd.Timestamp("2022-01-01") + pd.DateOffset(months=9 * i)
            rows.append({"pwsid": "XX0000001", "violation_id": str(i), "violation_category_code": cat,
                         "is_health_based_ind": hb, "contaminant_code": code,
                         "compliance_status_code": st,
                         "compl_per_begin_date": b.strftime("%Y-%m-%d"),
                         "compl_per_end_date": (b + pd.DateOffset(months=3)).strftime("%Y-%m-%d"),
                         "viol_measure": "0.091" if cat == "MCL" else None,
                         "unit_of_measure": "mg/L" if cat == "MCL" else None,
                         "state_mcl": "0.08" if cat == "MCL" else None})
        return pd.DataFrame(rows)
    if table == "lcr_sample_result":
        return pd.DataFrame([{"pwsid": "XX0000001", "sample_id": f"L{i}",
                              "contaminant_code": c, "sample_measure": v, "unit_of_measure": "mg/L"}
                             for i, (c, v) in enumerate([("PB90", "0.006"), ("CU90", "0.4"),
                                                         ("PB90", "0.018"), ("CU90", "0.5")])])
    if table == "lcr_sample":
        return pd.DataFrame([{"sample_id": f"L{i}", "sampling_end_date": d}
                             for i, d in enumerate(["2021-09-30", "2021-09-30",
                                                    "2024-09-30", "2024-09-30"])])
    return pd.DataFrame()


def ucmr_frame():
    rows = []
    for d in ["2024-03-01", "2024-06-01", "2024-09-01", "2024-12-01"]:
        for c, v, s in [("PFOA", "0.0062", "="), ("PFOS", "0.0031", "="), ("PFHxS", None, "<"),
                        ("PFNA", None, "<"), ("HFPO-DA", None, "<"), ("PFBS", "0.0045", "="),
                        ("lithium", "12", "=")]:
            rows.append({"PWSID": "XX0000001", "PWSName": "TESTVILLE WATER", "State": "XX",
                         "FacilityName": "PLANT 1", "SamplePointName": "EP1", "CollectionDate": d,
                         "Contaminant": c, "MRL": "0.004" if c != "lithium" else "9",
                         "Units": "µg/L", "AnalyticalResultsSign": s,
                         "AnalyticalResultValue": v})
    return pd.DataFrame(rows)
