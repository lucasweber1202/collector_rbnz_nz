# collector_rbnz_nz

Standalone Python 3.11 collector of two official Reserve Bank of New Zealand
predictors for the NZ inflation nowcast: the Official Cash Rate
(`INM.DP1.N`, table B2, daily close) and the Trade Weighted Index, 17-currency
basket (`EXRT.DS41.NZB17`, table B1, daily). History and current RBNZ
workbooks are stitched with an explicit no-conflict rule. Canonical
`metadata`, `time_series` and `logs` in schema `collector_rbnz_nz`, with
vintages and release classification.

```bash
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
COLLECTOR_DB_URL=postgresql+psycopg2://user:password@localhost:5432/database .venv/bin/python main.py
```

**Access:** RBNZ serves its statistics behind Cloudflare and admits automated
clients by allowlisting their static public IP. From a non-allowlisted network
every download is a challenge and the run fails at once with that message; it
never parses the challenge. See METHODOLOGY.md.

Tests: `pytest` (Spark grammar needs `java`); `COLLECTOR_TEST_PG_URL` for the
PostgreSQL write paths and the mocked end-to-end pipeline; `RBNZ_LIVE_SMOKE=1`
from an allowlisted machine for the live workbooks.
