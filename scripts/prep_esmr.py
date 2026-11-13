"""One-time (re-runnable) prep: eSMR history for California sites that ECHO doesn't cover.

ECHO already carries DMRs for California's NPDES permits. eSMR adds monitoring data for WDR and
other non-NPDES sites, but the state's API scans a 1-2 million row table per year for every
lookup (15-45 s each), far too slow for the app. This script pulls those sites' history once
into data_static/esmr_non_npdes.parquet; the app reads that and fetches only the current year live.

    python scripts/prep_esmr.py              # all years since 2006
    python scripts/prep_esmr.py --since 2016

Takes ~15-30 minutes (two table scans per year on the state's server).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from echo_pull.ca import waterboards as W  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "data_static" / "esmr_non_npdes.parquet"


def sql(q: str) -> pd.DataFrame:
    frames, off = [], 0
    while True:
        res = W._call("datastore_search_sql", {"sql": f"{q} LIMIT 32000 OFFSET {off}"}, timeout=300)
        recs = res.get("records", [])
        frames.append(pd.DataFrame(recs))
        off += len(recs)
        if len(recs) < 32000:
            break
    return pd.concat(frames, ignore_index=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", type=int, default=2006)
    a = ap.parse_args()
    ids = {y: r for y, r in W.esmr_resources().items() if y >= a.since}
    this_year = pd.Timestamp.today().year

    # 1. which places ever reported to eSMR
    places = set()
    for y, rid in sorted(ids.items()):
        t0 = time.time()
        d = sql(f'SELECT DISTINCT facility_place_id FROM "{rid}"')
        places |= set(pd.to_numeric(d["facility_place_id"], errors="coerce").dropna().astype(int))
        print(f"{y}: {len(d)} reporting places ({time.time() - t0:.0f}s)", flush=True)

    # 2. drop the ones with an active NPDES wastewater permit (ECHO covers those)
    orders = W._search(W.ORDERS, fields=["facility_id", "program_category", "status"],
                       max_rows=200_000)
    npdes = set(pd.to_numeric(orders.loc[(orders["program_category"] == "NPDESWW") &
                                         (orders["status"] == "Active"), "facility_id"],
                              errors="coerce").dropna().astype(int))
    keep = sorted(places - npdes)
    print(f"{len(places)} places in eSMR, {len(keep)} without an active NPDES permit", flush=True)

    # 3. their rows, year by year (current year is fetched live by the app)
    cols = ", ".join(f'"{c}"' for c in ["facility_place_id", "facility_name", *W.ESMR_FIELDS])
    frames = []
    for y, rid in sorted(ids.items()):
        if y >= this_year:
            continue
        t0 = time.time()
        inlist = ",".join(map(str, keep))
        d = sql(f'SELECT {cols} FROM "{rid}" WHERE facility_place_id IN ({inlist})')
        frames.append(d)
        print(f"{y}: {len(d)} rows ({time.time() - t0:.0f}s)", flush=True)
    df = pd.concat(frames, ignore_index=True)
    df["facility_place_id"] = pd.to_numeric(df["facility_place_id"], errors="coerce").astype("Int64")
    for c in ("result", "mdl", "ml", "rl"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.sort_values(["facility_place_id", "sampling_date"], kind="stable")
    for c in df.columns:
        if df[c].dtype == object:
            df[c] = df[c].astype("string")
    OUT.parent.mkdir(exist_ok=True)
    # sorted by place ID in small row groups: a lookup reads one group, not the file
    df.to_parquet(OUT, index=False, compression="zstd", row_group_size=20_000)
    print(f"wrote {OUT} ({OUT.stat().st_size / 1e6:.1f} MB, {len(df):,} rows, "
          f"{df['facility_place_id'].nunique()} places, through {this_year - 1})")


if __name__ == "__main__":
    main()
