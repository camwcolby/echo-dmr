import numpy as np
import pandas as pd
import pytest

from echo_pull.dw import dww, federal, standards as S, ucmr, viz_dw as DV
from tests import dw_fixtures as FX


@pytest.fixture
def fake_state(monkeypatch):
    monkeypatch.setattr(dww, "_get", FX.fake_get)
    monkeypatch.setitem(dww.STATES, "XX", [FX.BASE])


@pytest.fixture
def fake_epa(monkeypatch):
    monkeypatch.setattr(federal, "query", FX.fake_query)


# ---------------------------------------------------------------- standards
@pytest.mark.parametrize("name,key", [
    ("NITRATE", "nitrate"), ("NITRATE-NITRITE", "nitrate_nitrite"), ("NITRITE", "nitrite"),
    ("TTHM", "tthm"), ("TOTAL TRIHALOMETHANES", "tthm"), ("TOTAL HALOACETIC ACIDS (HAA5)", "haa5"),
    ("ARSENIC", "arsenic"), ("COMBINED RADIUM (-226 & -228)", "radium"),
    ("CHLORINE", "chlorine"), ("CHLORINE DIOXIDE", "chlorine_dioxide"),
    ("TETRACHLOROETHYLENE", "pce"), ("1,1,1-TRICHLOROETHANE", "tca111"),
    ("HARDNESS, TOTAL (AS CACO3)", None)])
def test_standard_matching(name, key):
    s = S.match(name)
    assert (s.key if s else None) == key


def test_unit_conversion():
    assert S.convert(80, "UG/L", "mg/L") == pytest.approx(0.08)
    assert S.convert(0.0062, "ug/L", "ng/L") == pytest.approx(6.2)
    assert np.isnan(S.convert(5, "pCi/L", "mg/L"))
    assert S.convert(5, "pCi/L", "pCi/L") == 5


# ---------------------------------------------------------------- state DWW connector
def test_parser_skips_layout_tables():
    t = dww.tables(FX.samples_page(), FX.BASE)
    found = dww._find(t, "sample", "collection")
    assert found is not None and len(found) == 10


def test_dww_end_to_end(fake_state):
    base = dww.resolve_base("XX")
    sysid = dww.find_system("XX0000001", base)
    assert sysid["tinwsys_is_number"] == "1234"
    res, info = dww.all_results(base, sysid, pd.Timestamp("2020-01-01").date(), workers=2)
    assert info["samples_found"] == 10 and info["failed"] == 0
    assert set(res["analyte"]) == {"TTHM", "NITRATE", "HARDNESS, TOTAL (AS CACO3)"}
    tthm = res[res["analyte"] == "TTHM"]
    assert tthm["unit"].iloc[0].lower() == "ug/l" and tthm["value"].between(49, 75).all()
    nit = res[res["analyte"] == "NITRATE"]
    assert nit["less_than"].sum() == 5 and "collected" in res and res["collected"].notna().all()


def test_results_vs_standards(fake_state):
    base = dww.resolve_base("XX")
    res, _ = dww.all_results(base, dww.find_system("XX0000001", base),
                             pd.Timestamp("2020-01-01").date(), workers=2)
    d = DV.prepare_results(res)
    p = DV.analyte_panels(d)
    assert p.iloc[0]["analyte"] == "NITRATE"                    # 12.5 mg/L > 10: riskiest
    assert p.iloc[0]["max_pct"] == pytest.approx(1.25)
    tthm = d[d["analyte"] == "TTHM"]
    assert tthm["v"].max() == pytest.approx(0.074) and tthm["std_basis"].iloc[0] == "LRAA"
    raa = DV.running_avg(tthm)
    assert len(raa) >= 1 and (raa < 0.08).all()
    other = DV.other_analytes(d)
    assert list(other["analyte"]) == ["HARDNESS, TOTAL (AS CACO3)"]
    for _, r in p.iterrows():
        DV.analyte_panel(d, r)                                   # builds without error


# ---------------------------------------------------------------- federal
def test_federal(fake_epa):
    prof = federal.profile("XX0000001")
    assert prof["pws_type"] == "Community" and prof["population_served_count"] == 24500
    v = federal.violations("XX0000001")
    assert len(v) == 4 and v["health_based"].sum() == 2
    assert (v["status"] == "Open").sum() == 2
    assert v.loc[v["category"] == "Maximum contaminant level", "viol_measure"].iloc[0] == 0.091
    assert DV.violations_timeline(v) is not None
    lc = federal.lead_copper("XX0000001")
    lead = lc[lc["analyte"] == "Lead"]
    assert len(lead) == 2 and lead["date"].notna().all()
    fig = DV.lead_copper_panel(lc, "Lead")
    over = [t for t in fig.data if t.marker and t.marker.symbol == "diamond"]
    assert over and over[0].y[0] == pytest.approx(18)           # 0.018 mg/L -> 18 µg/L > 15


# ---------------------------------------------------------------- UCMR
def test_ucmr_and_hazard_index(tmp_path):
    p = tmp_path / "u.parquet"
    FX.ucmr_frame().to_parquet(p)
    u = ucmr.load_pws("XX0000001", p)
    assert u.loc[u["pfas_key"] == "pfoa", "value_ng_l"].iloc[0] == pytest.approx(6.2)
    hi = ucmr.hazard_index(u)
    assert hi["hazard_index"].iloc[0] == pytest.approx(4.5 / 2000)  # only PFBS detected
    pf = DV.pfas_panel(u, S.PFAS[0])
    assert any(t.marker.symbol == "diamond" for t in pf.data if t.marker)  # 6.2 > 4
    t = DV.ucmr_table(u)
    assert set(t["Contaminant"]) >= {"PFOA", "lithium"}
