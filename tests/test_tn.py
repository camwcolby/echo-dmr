"""Tennessee Tableau export -> results schema (fixture shaped like a real TDEC export, Nov 2026)."""
import io
from pathlib import Path

import pandas as pd
import pytest

from echo_pull.dw import dww, tn_tableau as TN, viz_dw as DV

FIX = Path(__file__).parent / "fixtures" / "TN_export.tsv"


def _raw():
    return pd.read_csv(io.StringIO(FIX.read_bytes().decode("utf-16")), sep="\t", dtype=str)


def test_parse_values_units_dates():
    r = TN.to_results(_raw())
    assert list(r["analyte"]) == ["NITRATE", "ATRAZINE", "TTHM", "GROSS ALPHA"]   # padding stripped
    nit = r.iloc[0]
    assert nit["value"] == pytest.approx(1.2) and nit["unit"] == "MG/L" and not nit["less_than"]
    assert nit["collected"] == pd.Timestamp("2024-08-01")                    # '01/AUG/2024\n07:33'
    assert nit["sampling_point"] == "EP001" and nit["lab_sample_no"] == "AB123"


def test_zero_or_blank_result_is_non_detect_at_dl():
    r = TN.to_results(_raw())
    atr, alpha = r.iloc[1], r.iloc[3]
    for row, unit in [(atr, "UG/L"), (alpha, "PCI/L")]:
        assert row["less_than"] and pd.isna(row["value"])
        assert row["unit"] == unit and row["reporting_level"] > 0          # unit from the DL


def test_flows_into_charts():
    d = DV.prepare_results(TN.to_results(_raw()))
    tthm = d[d["std_key"].eq("tthm")].iloc[0] if d["std_key"].eq("tthm").any() else None
    assert tthm is not None and tthm["pct_std"] == pytest.approx(92 / 80)     # 92 ug/L vs 0.080 mg/L
    assert d.loc[d["analyte"] == "ATRAZINE", "nd"].all()


def test_changed_columns_say_so():
    with pytest.raises(TN.TNError):
        TN.to_results(pd.DataFrame({"foo": ["1"]}))


def test_tn_routed_to_tableau_not_dww():
    assert "TN" not in dww.STATES and "TN" not in dww.ACCESS_NOTES
    assert TN.labels_for("tn0000002", "Adamsville  Water System")[0] == \
        "TN0000002     ADAMSVILLE WATER SYSTEM"
