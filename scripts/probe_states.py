"""Check every state Drinking Water Watch site in echo_pull/dw/dww.py and report what works.

Run from the repo folder:
    python scripts/probe_states.py            # all states
    python scripts/probe_states.py TN CA KS   # just these

For each state it: finds a working base URL, picks a real community water system (from EPA's
federal data, or a known fallback), looks it up on the state site, lists recent chem/rad samples,
and reads one sample's results. Output:
    probe_report.csv          one row per state: which step passed or failed, and why
    probe_html/<ST>_*.html    the page that failed, so the parser can be fixed from it
Send probe_report.csv (and the probe_html folder, zipped) back to get broken states fixed.
"""
from __future__ import annotations

import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from echo_pull.dw import dww, federal  # noqa: E402

FALLBACK_PWSID = {  # real systems seen on each state's DWW (used if EPA's API is unreachable)
    "TN": "TN0008019", "NV": "NV0002216", "MD": "MD1020146", "AL": "AL0001336",
    "KY": "KY0690203", "VT": "VT0000341", "SC": "SC4660112", "NM": "NM3524626",
    "MS": "MS0340007", "NE": "NE3121112", "MT": "MT0005120", "MO": "MO1010001",
}
OUT_HTML = Path("probe_html")


def pick_pwsid(st: str) -> str | None:
    try:
        df = federal.search_systems(state=st)
        df = df[df.get("pws_type_code", "").astype(str).eq("CWS")] if "pws_type_code" in df else df
        if "pws_activity_code" in df:
            df = df[df["pws_activity_code"].astype(str).eq("A")]
        if not df.empty:
            mid = df.iloc[min(len(df) - 1, 20)]          # a mid-sized system, not the giant one
            return str(mid["pwsid"])
    except Exception as e:  # noqa: BLE001
        print(f"   (EPA lookup failed for {st}: {str(e)[:80]})")
    return FALLBACK_PWSID.get(st)


def save(st, step, html):
    OUT_HTML.mkdir(exist_ok=True)
    (OUT_HTML / f"{st}_{step}.html").write_text(html or "", encoding="utf-8")


def probe(st: str) -> dict:
    row = {"state": st, "base": "", "pwsid": "", "step_failed": "", "error": "",
           "samples_listed": 0, "results_rows": 0, "example_analytes": "", "seconds": 0}
    t0 = time.time()
    try:
        sess, base = dww.open_state(st)
        row["base"] = base
    except Exception as e:  # noqa: BLE001
        row.update(step_failed="site", error=str(e)[:300])
        return row
    row["pwsid"] = pwsid = pick_pwsid(st) or ""
    if not pwsid:
        row.update(step_failed="pick_pwsid", error="no PWSID available")
        return row
    try:
        sysid = dww.find_system(pwsid, base, sess)
    except Exception as e:  # noqa: BLE001
        row.update(step_failed="find_system", error=str(e)[:300])
        try:
            save(st, "search", dww._get(sess, f"{base}/JSP/WaterSystems.jsp?number={pwsid}"
                                              f"&name=&county=All&PointOfContactType=none"))
        except Exception:  # noqa: BLE001
            pass
        return row
    try:
        s = dww.sample_list(base, sysid, date.today() - timedelta(days=3 * 365), sess=sess)
        row["samples_listed"] = len(s)
        if s.empty:
            row.update(step_failed="sample_list", error="no samples parsed")
            n, c = sysid["tinwsys_is_number"], sysid["tinwsys_st_code"]
            save(st, "samples", dww._get(sess, f"{base}/JSP/NonTcrSamples.jsp?tinwsys_is_number="
                                               f"{n}&tinwsys_st_code={c}&history=1&counter=0"))
            return row
        r = dww.sample_results(s["result_url"].iloc[0], sess)
        row["results_rows"] = len(r)
        if r.empty or not (r["value"].notna() | r["less_than"]).any():
            row.update(step_failed="sample_results", error="no results parsed")
            save(st, "results", dww._get(sess, s["result_url"].iloc[0]))
        else:
            row["example_analytes"] = "; ".join(
                (f"{a} = {v:g} {u}" if v == v else f"{a} not detected ({u})")
                for a, v, u in r[["analyte", "value", "unit"]].head(3).values)
    except Exception as e:  # noqa: BLE001
        row.update(step_failed="samples", error=str(e)[:300])
    row["seconds"] = round(time.time() - t0, 1)
    return row


def main(states):
    skipped = [s for s in states if s in dww.OTHER_PLATFORM and s not in dww.STATES]
    for s in skipped:
        print(f"{s}: skipped, {dww.OTHER_PLATFORM[s]}")
    states = [s for s in states if s in dww.STATES]
    rows = []
    for st in states:
        print(f"{st} ...", flush=True)
        r = probe(st)
        ok = "OK" if not r["step_failed"] else f"FAILED at {r['step_failed']}: {r['error'][:90]}"
        print(f"   {ok}  samples={r['samples_listed']} results={r['results_rows']} "
              f"{r['example_analytes'][:80]}")
        rows.append(r)
    df = pd.DataFrame(rows)
    df.to_csv("probe_report.csv", index=False)
    good = df[df["step_failed"] == ""]["state"].tolist()
    print(f"\nWorking: {len(good)} of {len(df)}: {', '.join(good)}")
    print("Report: probe_report.csv" + ("   Failed pages: probe_html/" if OUT_HTML.exists() else ""))


if __name__ == "__main__":
    main([s.upper() for s in sys.argv[1:]] or sorted(dww.STATES))
