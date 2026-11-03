"""Federal drinking water standards (40 CFR Part 141), for screening sample results.

These are FEDERAL values. States may adopt stricter or additional standards (California has many),
and compliance is determined by the state under the rule's own averaging and confirmation
procedures. Charts built on this table are a screen, not a compliance determination.

basis:
  single   - compared sample by sample (with confirmation sampling)
  RAA      - running annual average of quarterly results (IOCs/VOCs/SOCs/rads when on quarterly
             monitoring, bromate, MRDLs)
  LRAA     - locational running annual average, by sampling location (TTHM, HAA5 under Stage 2)
  P90      - 90th percentile of tap samples vs an action level (lead and copper)
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class Standard:
    key: str
    pattern: str          # regex matched against the analyte name (case-insensitive)
    value: float
    unit: str             # unit of `value`
    kind: str             # MCL, MRDL, Action level
    basis: str            # single, RAA, LRAA, P90
    group: str
    status: str = ""      # regulatory status note, shown on charts
    cite: str = "40 CFR 141"


S = Standard
STANDARDS: list[Standard] = [
    # ---- inorganics (141.62)
    S("nitrate_nitrite", r"^nitrate[-\s+&/]*nitrite", 10, "mg/L", "MCL", "single", "Inorganic", cite="40 CFR 141.62"),
    S("nitrate", r"^nitrate(?!.*nitrite)", 10, "mg/L", "MCL", "single", "Inorganic", cite="40 CFR 141.62"),
    S("nitrite", r"^nitrite", 1, "mg/L", "MCL", "single", "Inorganic", cite="40 CFR 141.62"),
    S("antimony", r"^antimony", 0.006, "mg/L", "MCL", "RAA", "Inorganic"),
    S("arsenic", r"^arsenic", 0.010, "mg/L", "MCL", "RAA", "Inorganic"),
    S("barium", r"^barium", 2, "mg/L", "MCL", "RAA", "Inorganic"),
    S("beryllium", r"^beryllium", 0.004, "mg/L", "MCL", "RAA", "Inorganic"),
    S("cadmium", r"^cadmium", 0.005, "mg/L", "MCL", "RAA", "Inorganic"),
    S("chromium", r"^chromium(?!.*hexa)", 0.1, "mg/L", "MCL", "RAA", "Inorganic"),
    S("cyanide", r"^cyanide", 0.2, "mg/L", "MCL", "RAA", "Inorganic"),
    S("fluoride", r"^fluoride", 4.0, "mg/L", "MCL", "RAA", "Inorganic"),
    S("mercury", r"^mercury", 0.002, "mg/L", "MCL", "RAA", "Inorganic"),
    S("selenium", r"^selenium", 0.05, "mg/L", "MCL", "RAA", "Inorganic"),
    S("thallium", r"^thallium", 0.002, "mg/L", "MCL", "RAA", "Inorganic"),
    # ---- disinfection byproducts and disinfectants (141.64, 141.65)
    S("tthm", r"(^tthm|total trihalomethanes|^trihalomethanes)", 0.080, "mg/L", "MCL", "LRAA", "DBP", cite="40 CFR 141.64"),
    S("haa5", r"(haa5|haloacetic acids)", 0.060, "mg/L", "MCL", "LRAA", "DBP", cite="40 CFR 141.64"),
    S("bromate", r"^bromate", 0.010, "mg/L", "MCL", "RAA", "DBP", cite="40 CFR 141.64"),
    S("chlorite", r"^chlorite", 1.0, "mg/L", "MCL", "single", "DBP", cite="40 CFR 141.64"),
    S("chlorine", r"^(chlorine(?! dioxide)|free chlorine|total chlorine)", 4.0, "mg/L", "MRDL", "RAA", "Disinfectant", cite="40 CFR 141.65"),
    S("chloramine", r"^chloramine", 4.0, "mg/L", "MRDL", "RAA", "Disinfectant", cite="40 CFR 141.65"),
    S("chlorine_dioxide", r"^chlorine dioxide", 0.8, "mg/L", "MRDL", "single", "Disinfectant", cite="40 CFR 141.65"),
    # ---- radionuclides (141.66)
    S("gross_alpha", r"gross alpha", 15, "pCi/L", "MCL", "RAA", "Radionuclide", cite="40 CFR 141.66"),
    S("radium", r"combined radium|radium.*226.*228", 5, "pCi/L", "MCL", "RAA", "Radionuclide", cite="40 CFR 141.66"),
    S("uranium", r"^(combined )?uranium", 0.030, "mg/L", "MCL", "RAA", "Radionuclide", cite="40 CFR 141.66"),
    # ---- volatile organics (141.61(a))
    S("benzene", r"^benzene$", 0.005, "mg/L", "MCL", "RAA", "VOC", cite="40 CFR 141.61"),
    S("carbon_tet", r"carbon tetrachloride", 0.005, "mg/L", "MCL", "RAA", "VOC", cite="40 CFR 141.61"),
    S("chlorobenzene", r"^(mono)?chlorobenzene", 0.1, "mg/L", "MCL", "RAA", "VOC", cite="40 CFR 141.61"),
    S("o_dcb", r"(o-|1,2-)dichlorobenzene", 0.6, "mg/L", "MCL", "RAA", "VOC", cite="40 CFR 141.61"),
    S("p_dcb", r"(p-|1,4-)dichlorobenzene", 0.075, "mg/L", "MCL", "RAA", "VOC", cite="40 CFR 141.61"),
    S("dca12", r"1,2-dichloroethane", 0.005, "mg/L", "MCL", "RAA", "VOC", cite="40 CFR 141.61"),
    S("dce11", r"1,1-dichloroethylene|1,1-dichloroethene", 0.007, "mg/L", "MCL", "RAA", "VOC", cite="40 CFR 141.61"),
    S("cis_dce", r"cis-1,2-dichloroethylene", 0.07, "mg/L", "MCL", "RAA", "VOC", cite="40 CFR 141.61"),
    S("trans_dce", r"trans-1,2-dichloroethylene", 0.1, "mg/L", "MCL", "RAA", "VOC", cite="40 CFR 141.61"),
    S("dcm", r"^(dichloromethane|methylene chloride)", 0.005, "mg/L", "MCL", "RAA", "VOC", cite="40 CFR 141.61"),
    S("dcp12", r"1,2-dichloropropane", 0.005, "mg/L", "MCL", "RAA", "VOC", cite="40 CFR 141.61"),
    S("ethylbenzene", r"^ethylbenzene", 0.7, "mg/L", "MCL", "RAA", "VOC", cite="40 CFR 141.61"),
    S("styrene", r"^styrene", 0.1, "mg/L", "MCL", "RAA", "VOC", cite="40 CFR 141.61"),
    S("pce", r"(tetrachloroethylene|tetrachloroethene)", 0.005, "mg/L", "MCL", "RAA", "VOC", cite="40 CFR 141.61"),
    S("toluene", r"^toluene", 1, "mg/L", "MCL", "RAA", "VOC", cite="40 CFR 141.61"),
    S("tcb124", r"1,2,4-trichlorobenzene", 0.07, "mg/L", "MCL", "RAA", "VOC", cite="40 CFR 141.61"),
    S("tca111", r"1,1,1-trichloroethane", 0.2, "mg/L", "MCL", "RAA", "VOC", cite="40 CFR 141.61"),
    S("tca112", r"1,1,2-trichloroethane", 0.005, "mg/L", "MCL", "RAA", "VOC", cite="40 CFR 141.61"),
    S("tce", r"(trichloroethylene|trichloroethene)", 0.005, "mg/L", "MCL", "RAA", "VOC", cite="40 CFR 141.61"),
    S("vinyl_chloride", r"vinyl chloride", 0.002, "mg/L", "MCL", "RAA", "VOC", cite="40 CFR 141.61"),
    S("xylenes", r"xylenes?,? total|^total xylenes|^xylenes$", 10, "mg/L", "MCL", "RAA", "VOC", cite="40 CFR 141.61"),
    # ---- synthetic organics (141.61(c))
    S("alachlor", r"^alachlor", 0.002, "mg/L", "MCL", "RAA", "SOC", cite="40 CFR 141.61"),
    S("atrazine", r"^atrazine", 0.003, "mg/L", "MCL", "RAA", "SOC", cite="40 CFR 141.61"),
    S("bap", r"benzo\(a\)pyrene", 0.0002, "mg/L", "MCL", "RAA", "SOC", cite="40 CFR 141.61"),
    S("24d", r"^2,4-d\b", 0.07, "mg/L", "MCL", "RAA", "SOC", cite="40 CFR 141.61"),
    S("dehp", r"di\(2-ethylhexyl\)\s*phthalate", 0.006, "mg/L", "MCL", "RAA", "SOC", cite="40 CFR 141.61"),
    S("glyphosate", r"^glyphosate", 0.7, "mg/L", "MCL", "RAA", "SOC", cite="40 CFR 141.61"),
    S("lindane", r"^(lindane|gamma-bhc)", 0.0002, "mg/L", "MCL", "RAA", "SOC", cite="40 CFR 141.61"),
    S("pcp", r"^pentachlorophenol", 0.001, "mg/L", "MCL", "RAA", "SOC", cite="40 CFR 141.61"),
    S("simazine", r"^simazine", 0.004, "mg/L", "MCL", "RAA", "SOC", cite="40 CFR 141.61"),
    S("edb", r"(ethylene dibromide|1,2-dibromoethane)", 0.00005, "mg/L", "MCL", "RAA", "SOC", cite="40 CFR 141.61"),
    S("dbcp", r"(dbcp|1,2-dibromo-3-chloropropane)", 0.0002, "mg/L", "MCL", "RAA", "SOC", cite="40 CFR 141.61"),    S("silvex", r"(2,4,5-tp|silvex)", 0.05, "mg/L", "MCL", "RAA", "SOC", cite="40 CFR 141.61"),
    S("carbofuran", r"^carbofuran", 0.04, "mg/L", "MCL", "RAA", "SOC", cite="40 CFR 141.61"),
    S("chlordane", r"chlordane", 0.002, "mg/L", "MCL", "RAA", "SOC", cite="40 CFR 141.61"),
    S("dalapon", r"^dalapon", 0.2, "mg/L", "MCL", "RAA", "SOC", cite="40 CFR 141.61"),
    S("deha", r"di\(2-ethylhexyl\)\s*adipate", 0.4, "mg/L", "MCL", "RAA", "SOC", cite="40 CFR 141.61"),
    S("dinoseb", r"^dinoseb", 0.007, "mg/L", "MCL", "RAA", "SOC", cite="40 CFR 141.61"),
    S("diquat", r"^diquat", 0.02, "mg/L", "MCL", "RAA", "SOC", cite="40 CFR 141.61"),
    S("endothall", r"^endothall", 0.1, "mg/L", "MCL", "RAA", "SOC", cite="40 CFR 141.61"),
    S("endrin", r"^endrin", 0.002, "mg/L", "MCL", "RAA", "SOC", cite="40 CFR 141.61"),
    S("heptachlor", r"^heptachlor(?!\s*epoxide)", 0.0004, "mg/L", "MCL", "RAA", "SOC", cite="40 CFR 141.61"),
    S("heptachlor_epox", r"^heptachlor\s*epoxide", 0.0002, "mg/L", "MCL", "RAA", "SOC", cite="40 CFR 141.61"),
    S("hcb", r"^hexachlorobenzene", 0.001, "mg/L", "MCL", "RAA", "SOC", cite="40 CFR 141.61"),
    S("hccpd", r"^hexachlorocyclopentadiene", 0.05, "mg/L", "MCL", "RAA", "SOC", cite="40 CFR 141.61"),
    S("methoxychlor", r"^methoxychlor", 0.04, "mg/L", "MCL", "RAA", "SOC", cite="40 CFR 141.61"),
    S("oxamyl", r"^(oxamyl|vydate)", 0.2, "mg/L", "MCL", "RAA", "SOC", cite="40 CFR 141.61"),
    S("picloram", r"^picloram", 0.5, "mg/L", "MCL", "RAA", "SOC", cite="40 CFR 141.61"),
    S("pcb", r"(^pcbs?\b|polychlorinated biphenyl|total pcb)", 0.0005, "mg/L", "MCL", "RAA", "SOC", cite="40 CFR 141.61"),
    S("toxaphene", r"^toxaphene", 0.003, "mg/L", "MCL", "RAA", "SOC", cite="40 CFR 141.61"),
    S("tcdd", r"2,3,7,8-tcdd|dioxin", 3e-08, "mg/L", "MCL", "RAA", "SOC", cite="40 CFR 141.61"),
]

# Lead and copper: action levels on the 90th percentile of tap samples (141.80)
LEAD_AL = S("lead", r"^lead|^pb90$", 0.015, "mg/L", "Action level", "P90", "Lead & copper", cite="40 CFR 141.80")
COPPER_AL = S("copper", r"^copper|^cu90$", 1.3, "mg/L", "Action level", "P90", "Lead & copper", cite="40 CFR 141.80")

# PFAS (40 CFR 141, Subpart Z, April 2024 rule). Values in ng/L.
PFAS_STATUS_KEPT = ("2024 rule, retained. Compliance April 2029; EPA proposed (May 2026) letting "
                    "qualifying systems extend to 2031.")
PFAS_STATUS_RESCIND = "2024 rule; EPA proposed rescinding this standard (May 2026). Not final."
PFAS = [
    S("pfoa", r"^pfoa$|perfluorooctanoic", 4, "ng/L", "MCL", "RAA", "PFAS", PFAS_STATUS_KEPT, "40 CFR 141 (2024 PFAS NPDWR)"),
    S("pfos", r"^pfos$|perfluorooctane ?sulfonic", 4, "ng/L", "MCL", "RAA", "PFAS", PFAS_STATUS_KEPT, "40 CFR 141 (2024 PFAS NPDWR)"),
    S("pfhxs", r"^pfhxs$|perfluorohexane ?sulfonic", 10, "ng/L", "MCL", "RAA", "PFAS", PFAS_STATUS_RESCIND, "40 CFR 141 (2024 PFAS NPDWR)"),
    S("pfna", r"^pfna$|perfluorononanoic", 10, "ng/L", "MCL", "RAA", "PFAS", PFAS_STATUS_RESCIND, "40 CFR 141 (2024 PFAS NPDWR)"),
    S("hfpo_da", r"^hfpo-?da$|genx", 10, "ng/L", "MCL", "RAA", "PFAS", PFAS_STATUS_RESCIND, "40 CFR 141 (2024 PFAS NPDWR)"),
]
# Hazard index: sum of (concentration / health-based water concentration), MCL = 1 (unitless)
HI_HBWC_NGL = {"pfhxs": 9, "pfna": 10, "hfpo_da": 10, "pfbs": 2000}
HI_STATUS = PFAS_STATUS_RESCIND

_UNIT_TO_MG = {"mg/l": 1.0, "ug/l": 1e-3, "µg/l": 1e-3, "ng/l": 1e-6, "ppm": 1.0, "ppb": 1e-3,
               "ppt": 1e-6}


def match(analyte_name: str, pool: list[Standard] | None = None) -> Standard | None:
    name = (analyte_name or "").strip().lower()
    for s in (pool if pool is not None else STANDARDS):
        if re.search(s.pattern, name, flags=re.I):
            return s
    return None


def pfas_key(name: str) -> str | None:
    n = (name or "").strip().lower()
    for s in PFAS:
        if re.search(s.pattern, n, flags=re.I):
            return s.key
    if re.search(r"^pfbs$|perfluorobutane ?sulfonic", n):
        return "pfbs"
    return None


def convert(value, from_unit: str, to_unit: str):
    """Convert a concentration between mass units. Returns NaN if units are incompatible."""
    f = from_unit.strip().lower() if isinstance(from_unit, str) else ""
    t = to_unit.strip().lower() if isinstance(to_unit, str) else ""
    if not f or not t:
        return float("nan")
    if f == t:
        return value
    if f in _UNIT_TO_MG and t in _UNIT_TO_MG:
        return value * _UNIT_TO_MG[f] / _UNIT_TO_MG[t]
    return float("nan")


def table() -> pd.DataFrame:
    rows = STANDARDS + [LEAD_AL, COPPER_AL] + PFAS
    return pd.DataFrame([{"Contaminant": s.key, "Standard": s.value, "Unit": s.unit,
                          "Type": s.kind, "Compliance basis": s.basis, "Group": s.group,
                          "Status": s.status, "Citation": s.cite} for s in rows])
