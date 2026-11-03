"""Chart logic: seasonal limits, limit-in-force steps, % view rules."""
import numpy as np
import pandas as pd
import pytest

from echo_pull import analysis as A
from echo_pull import normalize, qa, viz
from tests import synth


def _seasonal_raw():
    """Two years of monthly ammonia with a 5 mg/L winter / 2 mg/L summer limit, then a
    renewal that tightens summer to 1.5 mg/L, plus a summer exceedance."""
    rows = []
    for t in pd.date_range("2022-01-31", "2024-12-31", freq="ME"):
        summer = 5 <= t.month <= 10
        lim = (1.5 if t.year == 2024 else 2.0) if summer else 5.0
        val = 2.6 if t == pd.Timestamp("2023-07-31") else 0.8
        rows.append(synth._row("Effluent Gross", "001", "00610", "Nitrogen, ammonia total [as N]",
                               t, "MO AVG", "mg/L", val, lim=lim, viol=val > lim))
        rows.append(synth._row("Effluent Gross", "001", "00400", "pH", t, "MINIMUM", "SU",
                               6.6, lim=6.0, qual=">="))
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def seasonal():
    df = qa.apply(normalize.normalize(_seasonal_raw()))
    return df, viz.panel_list(df, A.headroom(df))


def _row(p, par):
    return p[p.parameter.str.startswith(par)].iloc[0]


def test_seasonal_story_and_renewal(seasonal):
    df, p = seasonal
    r = _row(p, "Nitrogen")
    story = viz.limit_story(viz._select(df, r), "mg/L")
    assert any(s.startswith("Seasonal limit: 5 mg/L Nov–Apr; 1.5 mg/L May–Oct") for s in story)
    assert any("Limit changed May 2024: 2 → 1.5 mg/L" in s for s in story)


def test_limit_step_follows_limit_in_force(seasonal):
    df, p = seasonal
    fig = viz.limit_panel(df, _row(p, "Nitrogen"), None, "value")
    lim = next(t for t in fig.data if t.name == "Permit limit")
    pts = [(x, y) for x, y in zip(lim.x, lim.y) if x is not None]
    at = lambda d: {y for x, y in pts if x == pd.Timestamp(d)}
    assert at("2023-07-01") == {2.0}          # summer
    assert at("2023-12-01") == {5.0}          # winter
    assert at("2023-11-01") == {2.0, 5.0}     # the step itself: Oct ends at 2, Nov starts at 5
    assert at("2024-07-01") == {1.5}          # after renewal
    viol = next(t for t in fig.data if t.name == "Violation")
    assert len(viol.x) == 1


def test_pct_view_uses_limit_in_force(seasonal):
    df, p = seasonal
    fig = viz.limit_panel(df, _row(p, "Nitrogen"), None, "pct")
    v = next(t for t in fig.data if t.name == "Violation")
    assert v.y[0] == pytest.approx(130)       # 2.6 against the 2.0 summer limit, not 5.0
    normal = next(t for t in fig.data if t.name == "Reported")
    assert max(normal.y) == pytest.approx(0.8 / 1.5 * 100)  # summer 2024: 0.8 vs 1.5


def test_ph_never_shown_as_ratio(seasonal):
    df, p = seasonal
    fig = viz.limit_panel(df, _row(p, "pH"), None, "pct")
    pts = next(t for t in fig.data if t.name == "Reported")
    assert max(pts.y) == pytest.approx(6.6)
    assert any("isn't meaningful" in a.text for a in fig.layout.annotations)


def test_month_runs_wrap_the_year():
    m = {k: (5.0 if k in (11, 12, 1, 2, 3, 4) else 2.0) for k in range(1, 13)}
    runs = viz._month_runs(m)
    assert sorted((ms[0], ms[-1], v) for ms, v in runs) == [(5, 10, 2.0), (11, 4, 5.0)]


def test_annual_loads_from_synth():
    df = qa.apply(normalize.normalize(synth.build()))
    L = A.loads(df)
    t = viz.annual_table(L)
    assert list(t.index) == [2019, 2020, 2021, 2022, 2023]
    fig = viz.annual_panel(L, "BOD_lb_d", "Influent BOD load", "lb/d", None)
    assert fig is not None and len(fig.data[1].x) == 5
    assert viz.annual_panel(L, "TSS_lb_d", "Influent TSS load", "lb/d", None) is None
