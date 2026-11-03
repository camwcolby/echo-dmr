# ECHO pursuit tool

Pulls up to 10 years of DMR data for an NPDES permit from EPA ECHO, cleans it, checks it for data-entry
errors, ranks every permit limit by risk, and writes an Excel workbook for pursuit/pricing work.

## Setup (PowerShell, once)
```
cd "C:\Users\ccolby\OneDrive - Azuria\Documents\echo_tool\echo_tool"
pip install -r requirements.txt
python -m pytest -q tests        # should say 18 passed
```

Optional but recommended: keep the database OUT of OneDrive so sync can't lock it mid-write.
```
[Environment]::SetEnvironmentVariable("ECHO_DATA_DIR", "C:\echo_data", "User")
```
(close and reopen PowerShell after). Workbooks then land in `C:\echo_data\reports\`.

## Use
```
python echo.py search --name "Gatlinburg" --state TN      # find the permit ID
python echo.py pull TN0020117                             # pull 10 yrs from EPA (cached 7 days)
python echo.py load TN0020117 some_echo_download.csv      # or load a CSV you already have
python echo.py check TN0020117                            # quick printout: coverage, QA, risks
python echo.py analyze TN0020117 --design-mgd 4.5         # full analysis -> Excel workbook
```
`--design-mgd` is optional; with it, the flow-vs-violation table uses design flow as the break point.

## Risk classes (Risk ranking sheet)
| class | meaning |
|---|---|
| chronic | median of the last 3 yrs is at/above 75% of the limit. Steady pressure: chemicals, attention, cost. |
| episodic | recent violations or p95 over the limit, but typical months are fine. Usually wet weather / upsets. |
| watch | p95 at/above 75%, or median at/above 50% and rising 5+ pts of limit per year (see `yrs_to_75`). |
| check_reporting | half or more of values sit exactly at the limit; likely non-detects reported at the limit. |
| comfortable | none of the above. |
| inactive | limit has no data in the last 12 months (removed or changed at permit renewal). |

pH, percent removal and DO are judged by distance to the limit (SU, pct points, mg/L), not by a ratio.

## Data-quality flags (QA sheets)
Raw `value` is never changed. `value_clean` is what the analysis uses.

| flag | what happened |
|---|---|
| load_mismatch | monthly load 3x+ off from conc x flow (typo-scale). Replaced with conc x flow. |
| load_mismatch_rough | weekly/daily load 3x+ off. Blanked. |
| rolling_mismatch | rolling-avg load 2x+ off from the rebuilt rolling mean. Replaced. |
| rolling_filled | rolling-avg load missing; rebuilt from monthly loads. |
| load_conc_disagree | monthly load 35%-3x off. **Kept**: can be real in wet weather. Review. |
| spike_vs_history | 10x+ the series' own 13-month median. **Kept**. Review. |
| at_limit | value exactly equals its limit. **Kept**. Review. |

QA runs every time you `check` or `analyze`, so improvements never require re-pulling from EPA.

## If a pull breaks
ECHO column names are mapped in `ALIASES` in `echo_pull/normalize.py`. A "missing expected columns" error
prints what ECHO actually sent. The untouched download is kept in `data/raw/<permit>_raw.csv`.

## Web page (streamlit_app.py)
Type an NPDES ID; the page pulls up to 10 years from EPA ECHO and shows three sections:
- **Flow & loads**: annual average flow, influent BOD load and influent TSS load (monthly dots,
  annual line, table). Loads are influent monthly-average concentration x flow x 8.34.
- **Permit limits**: one small chart per limit, riskiest first. The limit is drawn as the limit in
  force each month, so seasonal limits and permit-renewal changes show where they applied, and are
  spelled out under each chart. Toggle to "% of limit in force" to compare across parameters
  (pH, DO and % removal stay in their own units). Filters for location and parameter.
- **Download**: raw ECHO CSV, the cleaned CSV the charts use, and the annual loads table.

Run on your own machine:  `streamlit run streamlit_app.py`  (opens in your browser)

Host it so others can use it (Streamlit Community Cloud, free):
1. Put this folder in a **private** GitHub repo (the .gitignore keeps data/ and reports out).
2. Go to share.streamlit.io, sign in with GitHub, click **Create app**, pick the repo,
   branch `main`, main file `streamlit_app.py`, then **Deploy**.
3. In the app's **Share** menu choose "Only specific people can view this app" and invite
   colleagues by email. They sign in with Google or a one-time emailed link.

Notes: the free tier allows one private app per account. Apps sleep after 12 hours with no
visitors; the next visitor clicks to wake it (about 30 seconds). Pushing to GitHub redeploys.

## Drinking water mode
Switch to **Drinking water (PWSID)** at the top of the app. Type a PWSID (or search by name) and get:
- **Violations**: every federal SDWIS violation on a timeline, health-based vs monitoring/reporting.
- **Lead & copper**: 90th percentile results vs the federal action levels.
- **Sample results (state)**: every chem/rad sample result from the state's Drinking Water Watch
  site, one chart per analyte vs the federal MCL, with running annual averages where the rule
  averages. Works for states listed in `echo_pull/dw/dww.py`; check them with
  `python scripts/probe_states.py` (writes probe_report.csv).
- **PFAS (UCMR 5)**: UCMR 5 results vs the 2024 PFAS MCLs and hazard index, with the status of
  EPA's May 2026 proposals. Needs the one-time prep below.
- **Standards**: the federal values the charts compare against (40 CFR 141).

### One-time UCMR prep
1. Download "UCMR 5 Occurrence Data Text Files" (ucmr5-occurrence-data.zip) from
   https://www.epa.gov/dwucmr/occurrence-data-unregulated-contaminant-monitoring-rule
2. `python scripts/prep_ucmr.py "C:\Users\you\Downloads\ucmr5-occurrence-data.zip"`
3. `git add data_static/ucmr5_slim.parquet`, commit, push. The web app picks it up on redeploy.
