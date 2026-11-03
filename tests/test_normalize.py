"""Synthetic rows shaped like ECHO's effluent-chart CSV. Swap in a real raw CSV once we have one."""
import pandas as pd
import pytest

from echo_pull import normalize, store

RAW = pd.DataFrame({
    "npdes_id": ["xx0000001"] * 7,
    "perm_feature_nmbr": ["001"] * 7,
    "monitoring_location_desc": ["Effluent Gross"] * 6 + ["Raw Sewage Influent"],
    "parameter_code": ["00310", "00310", "00300", "00530", "00530", "50050", "00310"],
    "parameter_desc": ["BOD, 5-day, 20 deg. C"] * 2 + ["Oxygen, dissolved [DO]",
                       "Solids, total suspended", "Solids, total suspended", "Flow",
                       "BOD, 5-day, 20 deg. C"],
    "monitoring_period_end_date": ["01/31/2024", "07/31/2024", "01/31/2024",
                                   "01/31/2024", "01/31/2024", "01/31/2024", "01/31/2024"],
    "limit_value_type_code": ["C1", "C1", "C3", "C1", "Q1", "Q1", "C1"],
    "statistical_base_short_desc": ["MO AVG", "MO AVG", "DAILY MN", "MO AVG", "MO AVG",
                                    "MO AVG", "MO AVG"],
    "limit_value_qualifier_code": ["<=", "<=", ">=", "<=", "<=", "", ""],
    "limit_value_standard_units": ["30", "30", "5", "30", "", "", ""],
    "dmr_value_standard_units": ["36", "12", "4", "", "250", "1.8", "210"],
    "standard_unit_desc": ["mg/L", "mg/L", "mg/L", "mg/L", "kg/d", "MGD", "mg/L"],
    # decoys in a different unit family: must NOT be used for value/limit
    "dmr_value_nmbr": ["999"] * 7,
    "limit_value_nmbr": ["1"] * 7,
    "nodi_code": ["", "", "", "C", "", "", ""],
    "violation_code": ["E90", "", "E90", "", "D80", "", ""],
    "violation_desc": ["DMR, Limited - Numeric Violation", "", "DMR, Limited - Numeric Violation",
                       "", "DMR Non-Receipt", "", ""],
})


def test_normalize_basics():
    df = normalize.normalize(RAW)
    assert len(df) == 7  # influent BOD kept separate from effluent BOD
    assert (df["npdes_id"] == "XX0000001").all()
    eff = df[df.monitoring_location == "Effluent Gross"]
    bod_jan = eff[(eff.parameter_code == "00310") & (eff.month == 1)].iloc[0]
    assert bod_jan.pct_of_limit == pytest.approx(1.2)
    assert bod_jan.is_violation and bod_jan.season == "winter"
    bod_jul = df[(df.parameter_code == "00310") & (df.month == 7)].iloc[0]
    assert bod_jul.pct_of_limit == pytest.approx(0.4) and not bod_jul.is_violation


def test_minimum_limits_flip():
    do = normalize.normalize(RAW).query("parameter_code == '00300'").iloc[0]
    assert do.limit_direction == "min"
    assert do.pct_of_limit == pytest.approx(1.25)  # 4 mg/L against a 5 mg/L floor
    assert do.is_violation


def test_nodi_and_load_basis():
    df = normalize.normalize(RAW)
    tss_c = df[(df.parameter_code == "00530") & (df.basis == "conc")].iloc[0]
    assert not tss_c.is_reported and tss_c.nodi_code == "C"
    assert set(df.basis) == {"conc", "load", "flow"}
    tss_q = df[(df.parameter_code == "00530") & (df.basis == "load")].iloc[0]
    assert tss_q.is_reporting_violation and not tss_q.is_violation  # late DMR != exceedance


def test_missing_columns_fail_loudly():
    with pytest.raises(ValueError, match="missing expected columns"):
        normalize.normalize(RAW.drop(columns=["dmr_value_standard_units", "dmr_value_nmbr"]))


def test_store_roundtrip(tmp_path):
    con = store.connect(tmp_path / "t.duckdb")
    df = normalize.normalize(RAW)
    store.save_dmr(con, "xx0000001", df, 10)
    store.save_dmr(con, "xx0000001", df, 10)  # re-pull must replace, not duplicate
    assert len(store.load_dmr(con, "XX0000001")) == 7
    assert store.last_pull(con, "XX0000001")[3] == 7


def test_standard_units_preferred():
    df = normalize.normalize(RAW)
    assert (df["unit_family"] == "standard").all()
    assert not (df["value"] == 999).any()
