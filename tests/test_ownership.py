"""Permit holder / owner / contact / operator-grade header lines."""
from echo_pull import client
from echo_pull.dw import dww, federal

BLUE_PLAINS = {"CWPName": "D.C. WASA (BLUE PLAINS)", "PermitName": "D.C. WASA (BLUE PLAINS)",
               "CWPFacilityTypeIndicator": "POTW", "CWPMajorMinorStatusFlag": "M",
               "CWPTotalDesignFlowNmbr": "370", "CWPActualAverageFlowNmbr": None,
               "CWPPermitStatusDesc": "Admin Continued", "CWPExpirationDate": "08/25/2023",
               "PermittingAgency": "U.S. EPA"}
JEFFERSON = {**BLUE_PLAINS, "PermitName": "Jefferson City", "CWPTotalDesignFlowNmbr": "2",
             "CWPActualAverageFlowNmbr": "1.37", "CWPPermitStatusDesc": "Effective",
             "CWPExpirationDate": "12/31/2029", "PermittingAgency": "State"}

# text as it appears on CA's Drinking Water Watch detail page for Mountain House (Nov 2026)
CA_DETAIL = """<table><tr><td>Water System No. :</td><td>CA3910027</td><td>Federal Type :</td><td>C</td></tr>
<tr><td>Status :</td><td>A</td><td>Activity Date :</td><td>11-13-1997</td></tr>
<tr><td>Distribution System Classification :</td><td>D3</td>
<td>Max Treatment Plant Classification :</td><td>T5</td></tr></table>"""


def test_permit_summary_admin_continued():
    s = client.permit_summary(BLUE_PLAINS)
    assert s[0] == "Permit holder: D.C. WASA (BLUE PLAINS)"
    assert "POTW major" in s and "design 370 MGD" in s
    assert s[-1] == "permit admin continued, expired Aug 2023 (U.S. EPA)"


def test_permit_summary_actual_flow_and_future_expiry():
    s = client.permit_summary(JEFFERSON)
    assert "design 2 MGD (actual avg 1.37)" in s
    assert s[-1] == "permit effective, expires Dec 2029 (State)"
    assert client.permit_summary({}) == []


def test_grades_from_state_detail_page():
    assert dww.classifications(CA_DETAIL) == {"Distribution System Classification": "D3",
                                              "Max Treatment Plant Classification": "T5"}
    assert dww.classifications("<p>Federal Type : C</p>") == {}


def test_owner_and_contact():
    o = federal.ownership({"owner_type_code": "L", "org_name": "CITY OF MOUNTAIN HOUSE",
                           "admin_name": "JANE DOE", "phone_number": "2099887423",
                           "email_addr": None, "address_line1": "251 E Main St.",
                           "city_name": "MOUNTAIN HOUSE", "state_code": "CA", "zip_code": "95391"})
    assert o["owner"] == "Local government" and o["phone"] == "(209) 988-7423"
    assert o["email"] is None and o["address"] == "251 E Main St., MOUNTAIN HOUSE, CA 95391"
    assert federal.ownership({}) == {}
