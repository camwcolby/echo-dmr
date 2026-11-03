"""A fake 5-year plant in ECHO's raw CSV shape, with known problems planted so tests can check
the QA and analysis layers find them."""
import numpy as np
import pandas as pd

PERMIT = "XX0000002"
MONTHS = pd.date_range("2019-01-31", "2023-12-31", freq="ME")
WET = {pd.Timestamp("2020-02-29"), pd.Timestamp("2021-03-31"), pd.Timestamp("2022-02-28"),
       pd.Timestamp("2023-03-31")}
TP_TYPO = pd.Timestamp("2021-04-30")        # monthly TP load entered ~400x too high
ROLL_TYPO = pd.Timestamp("2023-06-30")      # rolling TP load entered as 0.5 instead of ~6
K = 3.785


def _row(loc, outfall, pcode, param, t, stat, unit, val, lim=None, qual="<=", vt="C1", viol=False):
    return {
        "npdes_id": PERMIT, "perm_feature_nmbr": outfall, "monitoring_location_desc": loc,
        "parameter_code": pcode, "parameter_desc": param,
        "monitoring_period_end_date": t.strftime("%m/%d/%Y"),
        "limit_value_type_code": vt, "statistical_base_short_desc": stat,
        "limit_value_qualifier_code": qual if lim is not None else "",
        "limit_value_standard_units": "" if lim is None else str(lim),
        "dmr_value_standard_units": "" if val is None else f"{val:.6g}",
        "standard_unit_desc": unit, "nodi_code": "",
        "violation_code": "E90" if viol else "",
        "violation_desc": "DMR, Limited - Numeric Violation" if viol else "",
    }


def build() -> pd.DataFrame:
    rng = np.random.default_rng(7)
    rows, tp_loads = [], []
    for i, t in enumerate(MONTHS):
        wet = t in WET
        summer = t.month in (6, 7, 8)
        q = 2.5 + 0.15 * np.sin(2 * np.pi * (t.month - 2) / 12) + (0.6 if wet else 0) + 0.01 * i
        qpk = q * (2.8 if wet else 1.5)
        bod_inf = 220 * (1.25 if summer else 1.0) + rng.normal(0, 8)
        tss = 34.0 if wet else 9 + rng.normal(0, 1.5)
        tp_c = 0.65 + 0.05 * rng.normal()
        tp_kg = tp_c * q * K
        tp_loads.append(tp_kg)
        tp_reported = tp_kg * 400 if t == TP_TYPO else tp_kg

        E, I = "Effluent Gross", "Raw Sewage Influent"
        rows += [
            _row(E, "001", "50050", "Flow, in conduit or thru treatment plant", t, "MO AVG", "MGD",
                 q, vt="Q1"),
            _row(E, "001", "50050", "Flow, in conduit or thru treatment plant", t, "DAILY MX", "MGD",
                 qpk, vt="Q2"),
            _row(I, "INF1", "50050", "Flow, in conduit or thru treatment plant", t, "MO AVG", "MGD",
                 q, vt="Q1"),
            _row(I, "INF1", "80082", "BOD, carbonaceous [5 day, 20 C]", t, "MO AVG", "mg/L", bod_inf),
            _row(E, "001", "00530", "Solids, total suspended", t, "MO AVG", "mg/L", tss, lim=30,
                 viol=tss > 30),
            _row(E, "001", "00665", "Phosphorus, total [as P]", t, "MO AVG", "mg/L", tp_c),
            _row(E, "001", "00665", "Phosphorus, total [as P]", t, "MO AVG", "kg/d", tp_reported,
                 vt="Q1"),
            _row(E, "001", "50060", "Chlorine, total residual", t, "DAILY MX", "mg/L", 0.019,
                 lim=0.019),
        ]
        if i >= 11:
            r12 = float(np.mean(tp_loads[-12:]))
            rows.append(_row(E, "001", "00665", "Phosphorus, total [as P]", t, "ROLL AVG", "kg/d",
                             0.5 if t == ROLL_TYPO else r12, lim=6.8, vt="Q2"))
    return pd.DataFrame(rows)
