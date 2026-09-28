# RBNZ OCR and TWI — methodology

Authority: `guimasuko/collector_template` main `723f8633bbd367ad9cca0a199e84b10fd355da36`;
`NZD` is in its `metadata.country` vocabulary and is what this collector emits.
Structure, VERBATIM files and the three canonical tables follow the template;
no code is shared with or imported from another collector.

## Series (and why only these two)

| series_id | RBNZ ID | Table / workbooks | Unit | Why |
| --- | --- | --- | --- | --- |
| `RBNZ_B2_INM_DP1_N` | `INM.DP1.N` Official Cash Rate (OCR) | B2 wholesale interest rates, daily close: `hb2-daily-close-1985-2017.xlsx`, `hb2-daily-close.xlsx` | `percent` (`%pa`) | policy rate; monetary transmission to demand and inflation |
| `RBNZ_B1_EXRT_DS41_NZB17` | `EXRT.DS41.NZB17` TWI, 17-currency basket | B1 exchange rates and TWI, daily: `hb1-daily-1999-2017.xlsx`, `hb1-daily.xlsx` | `index` | import-price pass-through |

These are the two RBNZ series the governance universe lists as candidates. No
other series is added: NZD/USD is already collected (weekly, official MBIE
table), and bank-bill or swap rates would duplicate the policy-rate channel
without an objective case for the nowcast.

File URLs follow RBNZ's published pattern
`https://www.rbnz.govt.nz/-/media/project/sites/rbnz/files/statistics/series/b/<table>/<file>.xlsx`
(series pages: [B1](https://www.rbnz.govt.nz/statistics/series/exchange-and-interest-rates/exchange-rates-and-the-trade-weighted-index),
[B2](https://www.rbnz.govt.nz/statistics/series/exchange-and-interest-rates/wholesale-interest-rates)).

## Evidence for the IDs and the layout

RBNZ could not be reached from this environment (next section), so the
contract was built from evidence of the official files, not assumed:

- **Layout and OCR ID** — a genuine RBNZ B2 daily-close workbook (published
  2026-09-15, downloaded by its owner through a browser and kept byte-for-byte
  in its header rows) in the public repository `beezhub/fundamental-bias-engine`
  (`tests/fixtures/rbnz_hb2_daily_close.xlsx`): `Data` rows group / series /
  `Notes` / `Unit` / `Series Id`, then dates; `Series Definitions`
  (Group, Series, Series Id, Unit, Note); `Table Description` with
  `Published Date`. OCR = `INM.DP1.N`, unit `%pa`, 2.75 on 8–14 Sep 2026 and
  0.25 on 15 May 2020. The test workbooks reproduce this layout and these
  values; no RBNZ file is committed.
- **Workbook names and the B1/B2 split** — the maintained RBNZ R client
  (`rntq472/RBNZ`: `hb1-daily-1999-2017`, `hb1-daily`,
  `hb2-daily-close-1985-2017`, `hb2-daily-close`).
- **TWI ID** — `EXRT.DS41.NZB17`, documented by `cmhh/rbnz`, a tool that loads
  the official RBNZ workbooks into SQLite ("TWI, 17 currency basket").
- **Not yet observed:** the exact B1 unit string for the TWI. The collector
  accepts any non-empty unit that matches `Series Definitions`, stores the
  canonical `index` and keeps the source string in the description; a
  plausibility range (20–200) guards the values. The first allowlisted run
  (`RBNZ_LIVE_SMOKE=1`) closes this.

## Access (external block)

RBNZ statistics are behind Cloudflare. On 2026-09-27 every request from this
environment — `curl`, `httpx` with its default, an honest collector, or a
browser User-Agent, headless and headed Chromium, and a remote fetcher — got
`HTTP 403`, `cf-mitigated: challenge` ("Just a moment…"). The maintained RBNZ
R client documents the remedy: RBNZ allowlists the static public IP of an
automated client on request (contact on the
[terms of use](https://www.rbnz.govt.nz/about-our-site/terms-of-use) page), and
asks for a pause between downloads (the client waits 60 s;
`COLLECTOR_DOWNLOAD_DELAY` defaults to 60). The collector identifies itself
honestly and does not attempt to solve or evade the challenge.

`check_payload` turns a challenge (the `cf-mitigated` header or challenge
markup, any status) or any HTML into `SourceAccessError` naming the remedy;
the first blocked file stops the run, so nothing is parsed or written, and the
run log records the error.

## Validation

- `Data` must keep `Unit` in row 4 and `Series Id` in row 5; each selected ID
  must appear exactly once and is located by ID (neighbouring tenors are never
  read by position); its unit must match `Series Definitions`; OCR's unit
  must be `%pa`.
- `Published Date` must be present in `Table Description`.
- Values must be finite and plausible (OCR 0–25, TWI 20–200); a blank cell is a
  published date without a value and is absent, never 0.
- Duplicate dates fail. History and current workbooks are stitched: a date in
  both must carry the same value or the run stops (`SourceLayoutError`).
- Each series must end within 14 days of the run and span at least 5 years.

## Release monitoring

Per table, from the current workbook's `Published Date` and its latest date,
against `metadata` before the run and the rows changed: `first_release`,
`same_release`, `new_release`, `revised_source`, `layout_changed`.

## Point in time

RBNZ workbooks hold current history; RBNZ may revise or withdraw series.
`vintage_date` is the UTC collection date; the first backfill is dated the day
it was collected, never the observation or publication date. Later changes
become new vintages; same-day changes overwrite that day's vintage (template
rule). The OCR is a decision variable announced on scheduled dates; the daily
series carries the rate in force each business day.

## Verification (2026-09-27)

- Live run from this environment: fails at the first workbook with the
  Cloudflare/allowlisting `SourceAccessError`; run log `status=error`.
- PostgreSQL 16.13: the write-path suite (idempotent rerun, later-day vintage,
  same-day overwrite, metadata MERGE with NULL in every nullable column,
  time-series MERGE, run log, release classification) and a full
  `main.main()` run twice through a mocked RBNZ transport serving workbooks in
  the official layout (history + current, both tables): all rows stitched and
  written, the second run a no-op.
- All emitted SQL parses with the Spark SQL grammar (pyspark 4.1.1).
  **Databricks corporate runtime: not verified. Live RBNZ data: not verified
  (needs an allowlisted IP).**
