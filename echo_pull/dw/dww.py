"""State "Drinking Water Watch" (SDWIS/State public web) connector.

Many states run the same DWW web application with the same JSP pages and parameters:
  {base}/JSP/WaterSystems.jsp?number=<PWSID>&name=&county=All&PointOfContactType=none
  {base}/JSP/WaterSystemDetail.jsp?tinwsys_is_number=<n>&tinwsys_st_code=<ST>
  {base}/JSP/NonTcrSamples.jsp?tinwsys_is_number=<n>&tinwsys_st_code=<ST>&history=1&begin_date=..&end_date=..&counter=0
  {base}/JSP/NonTcrSampleResults.jsp?...&tsasampl_is_number=<n>&tsasampl_st_code=<ST>...
Chem/rad results live one page per sample, so results are fetched in parallel (politely) and cached.

STATES maps a state to candidate base URLs. Run scripts/probe_states.py to see which work, and
update the list from its report. A state that moves off DWW just needs its entry removed or fixed.
"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from urllib.parse import parse_qs, urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; inframark-dw-tool/0.1)"}

# Candidates per state, first that answers wins. Sourced from EPA's primacy agency data page and
# observed DWW URLs (Nov 2026). Unverified ones are marked; the probe script settles them.
STATES: dict[str, list[str]] = {
    "AK": ["https://dec.alaska.gov/dww"],
    "AL": ["http://dww.adem.alabama.gov/DWW"],
    "AZ": ["https://azsdwis.azdeq.gov/DWW_EXT"],
    "CA": ["https://sdwis.waterboards.ca.gov/PDWW"],
    "DE": ["https://drinkingwater.dhss.delaware.gov/DWW", "https://drinkingwater.dhss.delaware.gov"],
    "GA": ["http://gadrinkingwater.net/DWWPUB"],
    "ID": ["http://dww.deq.idaho.gov/IDPDWW"],
    "IL": ["https://water.epa.state.il.us/dww"],
    "KS": ["https://dww.kdhe.ks.gov/DWW"],
    "KY": ["https://dep.gateway.ky.gov/DWW"],
    "LA": ["https://sdw.ldh.la.gov/DWW", "https://sdw.ldh.la.gov"],
    "MD": ["https://mdesdwis.mde.state.md.us/DWW"],
    "MO": ["https://www.dnr.mo.gov/DWW"],
    "MS": ["https://apps.msdh.ms.gov/DWW"],
    "MT": ["https://sdwisdww.mt.gov/DWW", "http://sdwisdww.mt.gov:8080/DWW"],   # may have moved
    "NE": ["https://drinkingwater.ne.gov/DWW", "https://drinkingwater.ne.gov"],
    "NJ": ["https://www9.state.nj.us/DEP_WaterWatch_public"],
    "NM": ["https://dww.water.env.nm.gov", "https://dww.water.net.env.nm.gov/NMDWW"],
    "NV": ["https://ndwis.ndep.nv.gov/DWW"],
    "OK": ["http://sdwis.deq.state.ok.us/DWW"],
    "RI": ["https://dwq.health.ri.gov/DWW"],
    "SC": ["https://dww.des.sc.gov/DWW"],
    "TN": ["https://dataviewers.tdec.tn.gov/DWW"],
    "VT": ["https://anrnode.anr.state.vt.us/DWW"],
    "WY": ["https://sdwisdww.epa.gov/DWWR8WY"],
}


class DWWError(RuntimeError):
    pass


def supported(state: str) -> bool:
    return state.upper() in STATES


# ------------------------------------------------------------------ http
def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def _get(sess, url, timeout=45, retries=2) -> str:
    last = None
    for i in range(retries + 1):
        try:
            r = sess.get(url, timeout=timeout)
            if r.status_code == 200 and r.text:
                return r.text
            last = DWWError(f"HTTP {r.status_code} for {url}")
        except requests.RequestException as e:
            last = DWWError(f"{type(e).__name__} for {url}: {e}")
        time.sleep(1.0 * (i + 1))
    raise last


def _q(url: str) -> dict:
    return {k.lower(): v[0] for k, v in parse_qs(urlparse(url).query).items()}


# ------------------------------------------------------------------ table parsing
def _norm(h: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (h or "").strip().lower()).strip("_")


def tables(html: str, base_url: str = "") -> list[pd.DataFrame]:
    """Every HTML table that has a header row, as DataFrames with normalized column names.
    Each row also carries `_links`: absolute hrefs found in that row."""
    soup = BeautifulSoup(html, "lxml")
    out = []
    for t in soup.find_all("table"):
        # only this table's own rows, not rows of tables nested inside it (DWW nests layouts)
        rows = [tr for tr in t.find_all("tr") if tr.find_parent("table") is t]
        header, body = None, []
        for tr in rows:
            cells = tr.find_all(["th", "td"], recursive=False)
            if not cells:
                continue
            texts = [c.get_text(" ", strip=True) for c in cells]
            if header is None:
                looks_like_header = (len(cells) >= 3 and all(texts)
                                     and max(len(x) for x in texts) <= 60
                                     and (tr.find("th") is not None or not any(
                                         re.fullmatch(r"[\d\s.,:/-]+", x) for x in texts)))
                if looks_like_header:
                    header = [_norm(x) for x in texts]
                continue
            if len(cells) != len(header):
                continue
            links = [urljoin(base_url, a["href"]) for a in tr.find_all("a", href=True)]
            body.append(texts + [links])
        if header and body:
            df = pd.DataFrame(body, columns=header + ["_links"])
            out.append(df)
    return out


def _find(tbls, *must) -> pd.DataFrame | None:
    for t in tbls:
        if all(any(m in c for c in t.columns) for m in must):
            return t
    return None


def _col(df, *keys):
    for k in keys:
        for c in df.columns:
            if k in c:
                return c
    return None


# ------------------------------------------------------------------ steps
def resolve_base(state: str, sess=None) -> str:
    """First candidate base URL whose search page answers."""
    sess = sess or _session()
    errs = []
    for base in STATES.get(state.upper(), []):
        try:
            _get(sess, f"{base}/index.jsp", retries=0, timeout=20)
            return base
        except DWWError as e:
            errs.append(str(e))
    raise DWWError(f"No working DWW site for {state}: " + " | ".join(errs))


def find_system(pwsid: str, base: str, sess=None) -> dict:
    """PWSID -> {'tinwsys_is_number', 'tinwsys_st_code', 'detail_url'}."""
    sess = sess or _session()
    pwsid = pwsid.upper()
    url = (f"{base}/JSP/WaterSystems.jsp?number={pwsid}&name=&county=All"
           f"&PointOfContactType=none&WaterSystemType=All&SourceWaterType=All")
    html = _get(sess, url)
    soup = BeautifulSoup(html, "lxml")
    cands = []
    for a in soup.find_all("a", href=True):
        if "WaterSystemDetail.jsp" in a["href"]:
            q = _q(a["href"])
            if "tinwsys_is_number" in q:
                cands.append((q, urljoin(url, a["href"]), a.get_text(strip=True)))
    if not cands:
        raise DWWError(f"{pwsid} not found on {base} (no WaterSystemDetail links)")
    exact = [c for c in cands if pwsid in (c[2].upper() + c[0].get("wsnumber", "").upper())]
    q, href, _ = (exact or cands)[0]
    return {"tinwsys_is_number": q["tinwsys_is_number"],
            "tinwsys_st_code": q.get("tinwsys_st_code", pwsid[:2]), "detail_url": href}


def sample_list(base: str, sysid: dict, begin: date, end: date | None = None,
                sess=None, max_pages: int = 50) -> pd.DataFrame:
    """All chem/rad (non-TCR) samples in the date range, with the link to each result page."""
    sess = sess or _session()
    end = end or date.today()
    n, st = sysid["tinwsys_is_number"], sysid["tinwsys_st_code"]
    url = (f"{base}/JSP/NonTcrSamples.jsp?tinwsys_is_number={n}&tinwsys_st_code={st}"
           f"&history=1&begin_date={begin:%m/%d/%Y}&end_date={end:%m/%d/%Y}&counter=0")
    frames, seen = [], set()
    for _ in range(max_pages):
        if url in seen:
            break
        seen.add(url)
        html = _get(sess, url)
        t = _find(tables(html, url), "sample", "collection")
        if t is None:
            break
        frames.append(t)
        # pagination: follow a NonTcrSamples link with a higher counter, if the page offers one
        cur = int(_q(url).get("counter", 0) or 0)
        nxt = None
        for a in BeautifulSoup(html, "lxml").find_all("a", href=True):
            if "NonTcrSamples.jsp" in a["href"]:
                c = int(_q(a["href"]).get("counter", -1) or -1)
                if c > cur and (nxt is None or c < nxt[0]):
                    nxt = (c, urljoin(url, a["href"]))
        if not nxt:
            break
        url = nxt[1]
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["result_url"] = df["_links"].map(
        lambda ls: next((l for l in ls if "NonTcrSampleResults.jsp" in l), None))
    df = df.dropna(subset=["result_url"]).drop_duplicates("result_url")
    ren = {}
    for key, names in {"lab_sample_no": ("lab_sample",), "sample_type": ("type",),
                       "collected": ("collection",), "sampling_point": ("sampling_point",),
                       "location": ("location",), "laboratory": ("lab",)}.items():
        c = _col(df, *names)
        if c and c not in ren:
            ren[c] = key
    df = df.rename(columns=ren)
    if "collected" in df:
        df["collected"] = pd.to_datetime(df["collected"].str.extract(r"(\d{2}-\d{2}-\d{4})")[0],
                                         format="%m-%d-%Y", errors="coerce")
    return df.drop(columns=["_links"]).reset_index(drop=True)


_CONC = re.compile(r"(-?[\d.,]+(?:[eE]-?\d+)?)\s*([A-Za-zµ/%\d.]+)?")


def _value_unit(s: str):
    m = _CONC.search(str(s or ""))
    if not m:
        return float("nan"), ""
    try:
        v = float(m.group(1).replace(",", ""))
    except ValueError:
        v = float("nan")
    return v, (m.group(2) or "").replace("UG", "ug").replace("MG", "mg").replace("NG", "ng") \
        .replace("/L", "/L").replace("PCI", "pCi")


def sample_results(result_url: str, sess=None) -> pd.DataFrame:
    sess = sess or _session()
    html = _get(sess, result_url)
    t = _find(tables(html, result_url), "analyte", "concentration")
    if t is None:
        return pd.DataFrame()
    c_code, c_name = _col(t, "analyte_code"), _col(t, "analyte_name")
    c_lt, c_rl = _col(t, "less_than"), _col(t, "reporting_level")
    c_conc, c_mcl = _col(t, "concentration"), _col(t, "mcl")
    out = pd.DataFrame({
        "analyte_code": t[c_code] if c_code else "",
        "analyte": t[c_name] if c_name else "",
        "less_than": t[c_lt].str.contains("<|Y", regex=True) if c_lt else False,
    })
    vu = t[c_conc].map(_value_unit) if c_conc else pd.Series([(float("nan"), "")] * len(t))
    out["value"] = [v for v, _ in vu]
    out["unit"] = [u for _, u in vu]
    if c_rl:
        rl = t[c_rl].map(_value_unit)
        out["reporting_level"] = [v for v, _ in rl]
    if c_mcl:
        out["state_mcl_text"] = t[c_mcl]
    out["result_url"] = result_url
    return out


def all_results(base: str, sysid: dict, begin: date, max_samples: int = 400,
                workers: int = 4, progress=None) -> tuple[pd.DataFrame, dict]:
    """Sample list + every sample's results. Returns (results, info)."""
    sess = _session()
    samples = sample_list(base, sysid, begin, sess=sess)
    info = {"samples_found": len(samples), "samples_fetched": 0, "failed": 0,
            "truncated": len(samples) > max_samples}
    if samples.empty:
        return pd.DataFrame(), info
    samples = samples.sort_values("collected", ascending=False).head(max_samples) \
        if "collected" in samples else samples.head(max_samples)
    frames, done = [], 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(sample_results, u, _session()): u for u in samples["result_url"]}
        for f in as_completed(futs):
            done += 1
            try:
                r = f.result()
                if not r.empty:
                    frames.append(r)
            except DWWError:
                info["failed"] += 1
            if progress:
                progress(done / len(futs))
    info["samples_fetched"] = done - info["failed"]
    if not frames:
        return pd.DataFrame(), info
    res = pd.concat(frames, ignore_index=True)
    meta = [c for c in ("result_url", "lab_sample_no", "sample_type", "collected",
                        "sampling_point", "location") if c in samples]
    res = res.merge(samples[meta], on="result_url", how="left")
    return res, info
