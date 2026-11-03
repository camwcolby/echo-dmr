"""UCMR occurrence results, from the slim file built by scripts/prep_ucmr.py."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from . import standards as S

SLIM = Path(__file__).resolve().parents[2] / "data_static" / "ucmr5_slim.parquet"


def available() -> bool:
    return SLIM.exists()


def load_pws(pwsid: str, path: Path = SLIM) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_parquet(path, filters=[("PWSID", "==", pwsid.upper())])
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["CollectionDate"], errors="coerce", format="mixed")
    df["value"] = pd.to_numeric(df["AnalyticalResultValue"], errors="coerce")
    df["mrl"] = pd.to_numeric(df.get("MRL"), errors="coerce")
    df["detected"] = df["AnalyticalResultsSign"].astype(str).str.strip().eq("=") & df["value"].notna()
    unit = df.get("Units", pd.Series("µg/L", index=df.index)).astype(str)
    df["value_ng_l"] = [S.convert(v, u.replace("µ", "u"), "ng/L") if pd.notna(v) else np.nan
                        for v, u in zip(df["value"], unit)]
    df["mrl_ng_l"] = [S.convert(v, u.replace("µ", "u"), "ng/L") if pd.notna(v) else np.nan
                      for v, u in zip(df["mrl"], unit)]
    df["pfas_key"] = df["Contaminant"].map(S.pfas_key)
    return df.sort_values("date").reset_index(drop=True)


def hazard_index(df: pd.DataFrame) -> pd.DataFrame:
    """Hazard index per sample event (sampling point + date): sum(conc / HBWC), non-detects = 0."""
    d = df[df["pfas_key"].isin(S.HI_HBWC_NGL)].copy()
    if d.empty:
        return pd.DataFrame()
    d["term"] = np.where(d["detected"], d["value_ng_l"] / d["pfas_key"].map(S.HI_HBWC_NGL), 0.0)
    key = [c for c in ("SamplePointName", "FacilityName", "date") if c in d]
    hi = d.groupby(key, dropna=False).agg(hazard_index=("term", "sum"),
                                          n_analytes=("pfas_key", "nunique")).reset_index()
    return hi.sort_values("date")
