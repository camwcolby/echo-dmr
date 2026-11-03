"""Regression tests on real state pages captured by scripts/probe_states.py (Nov 2026)."""
from pathlib import Path

import pandas as pd
import pytest

from echo_pull.dw import dww, viz_dw as DV

FIX = Path(__file__).parent / "fixtures"


def _read(name):
    return (FIX / name).read_text(encoding="utf-8", errors="replace")


def test_kansas_javascript_links(monkeypatch):
    monkeypatch.setattr(dww, "_get", lambda *a, **k: _read("KS_search.html"))
    sysid = dww.find_system("KS2006108", "https://dww.kdhe.ks.gov/DWW")
    assert sysid["tinwsys_is_number"] == "482" and sysid["tinwsys_st_code"] == "KS"


def test_new_mexico_sample_table(monkeypatch):
    monkeypatch.setattr(dww, "_get", lambda *a, **k: _read("NM_samples.html"))
    s = dww.sample_list("https://dww.water.env.nm.gov",
                        {"tinwsys_is_number": "100", "tinwsys_st_code": "NM"},
                        pd.Timestamp("2025-01-01").date())
    assert len(s) == 64                     # every sample link on the page
    assert s["result_url"].str.contains("tsasampl_is_number=1023373").any()
    assert s["collected"].notna().all() and s["collected"].max() == pd.Timestamp("2026-09-22")
    assert "sampling_point" in s and s["sampling_point"].iloc[0].startswith("SP2030802")
    assert "ROSELAWN" in s["location"].iloc[0]


@pytest.mark.parametrize("page,analytes,unit", [
    ("ID_results.html", ["DI(2-ETHYLHEXYL) PHTHALATE"], "ug/L"),
    ("NE_results.html", ["PARAQUAT", "DIQUAT"], "ug/L"),
])
def test_non_detects_parse(monkeypatch, page, analytes, unit):
    monkeypatch.setattr(dww, "_get", lambda *a, **k: _read(page))
    r = dww.sample_results("https://x/JSP/NonTcrSampleResults.jsp?tsasampl_is_number=1")
    assert [a.strip() for a in r["analyte"]] == analytes
    assert r["less_than"].all() and r["value"].isna().all()
    assert (r["unit"].str.lower() == unit.lower()).all()        # unit taken from the RL
    assert r["reporting_level"].notna().all()


def test_non_detect_plots_at_reporting_level(monkeypatch):
    monkeypatch.setattr(dww, "_get", lambda *a, **k: _read("ID_results.html"))
    r = dww.sample_results("https://x/JSP/NonTcrSampleResults.jsp?tsasampl_is_number=1")
    r["collected"] = pd.Timestamp("2026-10-09")
    d = DV.prepare_results(r)
    row = d.iloc[0]
    assert row["std_key"] == "dehp" and row["nd"]
    assert row["rl"] == pytest.approx(0.0006)                     # 0.6 ug/L -> mg/L
    assert pd.isna(row["pct_std"])                                # non-detect: no % of MCL


def test_wyoming_region_step(monkeypatch):
    posts = []

    class FakeResp:
        status_code = 200

    def fake_post(self, url, data=None, timeout=None):
        posts.append((url, data))
        return FakeResp()

    monkeypatch.setattr(dww, "_get", lambda *a, **k: _read("WY_region.html"))
    monkeypatch.setattr(dww.requests.Session, "post", fake_post)
    sess, base = dww.open_state("WY")
    assert base == "https://sdwisdww.epa.gov/DWWR8WY"
    assert posts == [("https://sdwisdww.epa.gov/DWWR8WY/select-dww-region", {"state_code": "WY"})]


def test_moved_states_are_labelled_not_attempted():
    for st in ("LA", "MO", "MT", "GA", "RI"):
        assert st not in dww.STATES and st in dww.OTHER_PLATFORM


def test_dates_reposted_when_site_ignores_url_dates(monkeypatch):
    """NM shows the last 2 years on GET; the full range only comes back on a POST of the dates."""
    nm = _read("NM_samples.html")
    older = nm.replace("09-22-2026", "03-01-2019")         # pretend the POST returns older data

    class R:
        status_code = 200
        text = older

    calls = []
    monkeypatch.setattr(dww, "_get", lambda *a, **k: nm)
    monkeypatch.setattr(dww.requests.Session, "post",
                        lambda self, url, data=None, timeout=None: calls.append(data) or R())
    s = dww.sample_list("https://dww.water.env.nm.gov",
                        {"tinwsys_is_number": "100", "tinwsys_st_code": "NM"},
                        pd.Timestamp("2018-01-01").date(), sess=dww._session())
    assert calls and calls[0]["begin_date"] == "01/01/2018"
    assert s["collected"].min() == pd.Timestamp("2019-03-01")


def test_kansas_public_access(monkeypatch):
    """KS sample pages sit behind a login screen with a 'Public Access' button."""
    login, samples = _read("KS_login.html"), _read("NM_samples.html")
    state = {"logged_in": False}
    posts = []

    class R:
        status_code = 200
        text = samples

    def fake_post(self, url, data=None, timeout=None):
        posts.append(url)
        if url.endswith("/Login") and data.get("action") == "public":
            state["logged_in"] = True
        return R()

    monkeypatch.setattr(dww.requests.Session, "post", fake_post)
    monkeypatch.setattr(dww, "_get", lambda *a, **k: samples if state["logged_in"] else login)
    assert dww._is_login_page(login) and not dww._is_login_page(samples)
    s = dww.sample_list("https://dww.kdhe.ks.gov/DWW",
                        {"tinwsys_is_number": "482", "tinwsys_st_code": "KS"},
                        pd.Timestamp("2025-01-01").date(), sess=dww._session())
    assert posts[0] == "https://dww.kdhe.ks.gov/DWW/Login" and len(s) > 0
