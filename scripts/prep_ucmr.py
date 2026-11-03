"""One-time prep: slim EPA's UCMR 5 occurrence file into data_static/ucmr5_slim.parquet.

1. Download the UCMR 5 zip from EPA's UCMR occurrence data page:
   https://www.epa.gov/dwucmr/occurrence-data-unregulated-contaminant-monitoring-rule
   ("UCMR 5 Occurrence Data Text Files", ucmr5-occurrence-data.zip)
2. Run, from the repo folder:
       python scripts/prep_ucmr.py "C:\\Users\\you\\Downloads\\ucmr5-occurrence-data.zip"
   (a .zip or the extracted UCMR5_All.txt both work; a URL works too)
3. Commit data_static/ucmr5_slim.parquet (git add, commit, push) so the web app has it.

Re-run whenever EPA posts a new UCMR release.
"""
from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import pandas as pd

KEEP = ["PWSID", "PWSName", "State", "Size", "FacilityName", "FacilityWaterType",
        "SamplePointName", "SamplePointType", "CollectionDate", "SampleEventCode",
        "Contaminant", "MRL", "Units", "AnalyticalResultsSign", "AnalyticalResultValue"]
OUT = Path(__file__).resolve().parents[1] / "data_static" / "ucmr5_slim.parquet"


def _open(src: str) -> io.TextIOBase | Path:
    if src.lower().startswith("http"):
        import requests
        print("Downloading", src)
        r = requests.get(src, timeout=600)
        r.raise_for_status()
        data = io.BytesIO(r.content)
    else:
        p = Path(src)
        if p.suffix.lower() != ".zip":
            return p
        data = p.open("rb")
    z = zipfile.ZipFile(data)
    names = [n for n in z.namelist() if n.lower().endswith(".txt")]
    pick = next((n for n in names if "all" in n.lower()), None) or max(
        names, key=lambda n: z.getinfo(n).file_size)
    print("Reading", pick, "from zip")
    return io.TextIOWrapper(z.open(pick), encoding="latin-1", newline="")


def main(src: str) -> None:
    f = _open(src)
    head = (f.open("r", encoding="latin-1") if isinstance(f, Path) else f)
    first = head.readline()
    sep = "\t" if "\t" in first else ","
    cols = [c.strip() for c in first.rstrip("\r\n").split(sep)]
    use = [c for c in cols if c in KEEP]
    missing = [c for c in ("PWSID", "Contaminant", "AnalyticalResultValue", "CollectionDate")
               if c not in use]
    if missing:
        sys.exit(f"Expected columns not found: {missing}. Columns in file: {cols}")
    print(f"Columns kept: {use}")
    reader = pd.read_csv(head, sep=sep, names=cols, usecols=use, dtype=str,
                         chunksize=250_000, encoding_errors="replace")
    parts, n = [], 0
    for chunk in reader:
        n += len(chunk)
        parts.append(chunk)
        print(f"  {n:,} rows", end="\r")
    df = pd.concat(parts, ignore_index=True)
    for c in ("PWSID", "State", "Contaminant", "Units", "AnalyticalResultsSign", "Size",
              "FacilityWaterType", "SamplePointType", "SampleEventCode"):
        if c in df:
            df[c] = df[c].str.strip().astype("category")
    df = df.sort_values("PWSID")
    OUT.parent.mkdir(exist_ok=True)
    df.to_parquet(OUT, index=False, compression="zstd", row_group_size=50_000)
    print(f"\nWrote {len(df):,} rows, {df['PWSID'].nunique():,} systems -> {OUT} "
          f"({OUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
