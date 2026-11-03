"""QA + analysis on the synthetic 5-year plant (tests/synth.py), which has planted problems."""
import pandas as pd
import pytest

from echo_pull import analysis as A
from echo_pull import normalize, qa, report
from tests import synth


@pytest.fixture(scope="module")
def df():
    return qa.apply(normalize.normalize(synth.build()))


def _get(df, param, stat, unit, t):
    r = df[df.parameter.str.startswith(param) & (df.stat_base == stat) & (df.unit == unit)
           & (df.period_end == t)]
    assert len(r) == 1
    return r.iloc[0]


def test_gross_load_typo_repaired(df):
    r = _get(df, "Phosphorus", "MO AVG", "kg/d", synth.TP_TYPO)
    assert r.qa_flag == "load_mismatch"
    assert r.value > 1000 and r.value_clean == pytest.approx(r.qa_expected) and r.value_clean < 15


def test_rolling_misentry_rebuilt(df):
    r = _get(df, "Phosphorus", "ROLL AVG", "kg/d", synth.ROLL_TYPO)
    assert r.qa_flag == "rolling_mismatch" and r.value == 0.5 and r.value_clean > 5


def test_rolling_rebuild_ignores_the_typo(df):
    # the rolling rebuild must use the REPAIRED monthly load, or one typo poisons 12 months
    roll = df[(df.stat_base == "ROLL AVG") & df.parameter.str.startswith("Phosphorus")]
    assert roll["value_clean"].max() < 15


def test_no_false_alarms_on_clean_series(df):
    flagged = set(df.loc[df.qa_flag.notna(), "parameter"])
    assert "Solids, total suspended" not in flagged
    assert "BOD, carbonaceous [5 day, 20 C]" not in flagged


def test_at_limit_is_review_not_chronic(df):
    h = A.headroom(df)
    cl = h[h.parameter.str.startswith("Chlorine")].iloc[0]
    assert cl.risk_class == "check_reporting" and "non-detects" in cl.notes


def test_wet_weather_violations_are_episodic(df):
    h = A.headroom(df)
    tss = h[h.parameter.str.startswith("Solids")].iloc[0]
    assert tss.risk_class == "episodic" and tss.violations_all == len(synth.WET)


def test_unrepaired_spike_flagged_but_still_counted():
    raw = synth.build()
    raw.loc[(raw.parameter_desc == "Solids, total suspended")
            & (raw.monitoring_period_end_date == "06/30/2019"), "dmr_value_standard_units"] = "9000"
    d = qa.apply(normalize.normalize(raw))
    r = d[(d.parameter == "Solids, total suspended") & (d.period_end == "2019-06-30")].iloc[0]
    assert r.qa_flag == "spike_vs_history"  # flagged for review...
    assert r.is_violation  # ...but still a violation on face value: it's over the limit as reported


def test_ph_rule_does_not_catch_phosphorus():
    rule = A.MARGIN_RULES[0][0]
    s = pd.Series(["pH", "Phosphorus, total [as P]"])
    assert s.str.contains(rule, case=False).tolist() == [True, False]


def test_flow_vs_violations_with_design(df):
    fv = A.flow_vs_violations(df, design_mgd=4.0)
    assert fv["months"].sum() == len(synth.MONTHS)
    assert fv.iloc[-1]["pct_months_with_violation"] > fv.iloc[0]["pct_months_with_violation"]


def test_load_profile_finds_summer_peak(df):
    L = A.loads(df)
    assert L["profile"]["BOD_lb_d"].idxmax() in (6, 7, 8)
    assert not L["notes"]


def test_missing_influent_is_said_out_loud():
    raw = synth.build()
    raw = raw[raw.monitoring_location_desc != "Raw Sewage Influent"]
    L = A.loads(qa.apply(normalize.normalize(raw)))
    assert L["notes"] and "No influent" in L["notes"][0]


def test_report_writes(df, tmp_path):
    R = report.run(df, design_mgd=4.0)
    assert any(f.startswith("CHECK REPORTING") for f in report.findings(R))
    out = report.write_xlsx(R, tmp_path / "x.xlsx")
    assert out.exists() and out.stat().st_size > 5000
