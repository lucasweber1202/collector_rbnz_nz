"""Runtime settings loaded from environment variables.

A `.env` file in the repo root is auto-loaded if present. Every setting can
be overridden by exporting the matching environment variable.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
_ENV_FILE = ROOT_DIR / ".env"

if _ENV_FILE.exists():
    for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if value and key not in os.environ:
            os.environ[key] = value


SCHEMA_NAME = "collector_rbnz_nz"
CATALOG_NAME = "macrobond_inhouse"

# When the pipeline runs without an explicit --start-date, we look back this
# many months from the latest reference_date already stored to catch any
# late-arriving revisions without re-downloading the full archive.
START_DATE_LOOKBACK_MONTHS = 3

# When PROD is true, build_engine() uses the corporate Databricks engine
# (see scripts/databricks_engine.py). Otherwise it builds a SQLAlchemy
# engine from COLLECTOR_DB_URL -- which must point at a local SQL DB you
# control.
PROD = os.getenv("PROD", "false").lower() in ("1", "true", "yes")

DATABASE_URL = os.getenv("COLLECTOR_DB_URL", "")

# The OCR began on 17 March 1999; the history workbooks start earlier.
DEFAULT_START_DATE = date.fromisoformat(os.getenv("COLLECTOR_START_DATE", "1985-01-01"))

REQUEST_TIMEOUT = float(os.getenv("COLLECTOR_HTTP_TIMEOUT", "30"))
# RBNZ asks automated clients to leave time between downloads; the maintained
# RBNZ R client waits 60 seconds "to comply with new terms of service".
DOWNLOAD_DELAY = float(os.getenv("COLLECTOR_DOWNLOAD_DELAY", "60"))
MAX_RETRIES = int(os.getenv("COLLECTOR_MAX_RETRIES", "3"))
BACKOFF_FACTOR = float(os.getenv("COLLECTOR_BACKOFF_FACTOR", "2.0"))
# Identify honestly. RBNZ access for automated clients is granted by
# allowlisting the caller's static public IP (see METHODOLOGY.md); a browser
# User-Agent string would not change that and misrepresents the client.
USER_AGENT = os.getenv(
    "COLLECTOR_USER_AGENT",
    "collector_rbnz_nz/1.0 (+https://github.com/lucasweber1202/collector_rbnz_nz)",
)

LOG_LEVEL = os.getenv("COLLECTOR_LOG_LEVEL", "INFO")

# Databricks / Azure Key Vault settings (PROD only).
# DATABRICKS_TOKEN can be set directly to skip the Key Vault lookup;
# otherwise the token is fetched from AKV at engine-build time.
DBX_SERVER_HOSTNAME = os.getenv("DBX_SERVER_HOSTNAME", "")
DBX_HTTP_PATH = os.getenv("DBX_HTTP_PATH", "")
AKV_VAULT_URL = os.getenv("AKV_VAULT_URL", "")
AKV_SECRET_NAME = os.getenv("AKV_SECRET_NAME", "databricks-token")

# -- Source-specific constants below --------------------------------------
METADATA_TABLE = "metadata"
TIME_SERIES_TABLE = "time_series"
LOGS_TABLE = "logs"
COUNTRY_CURRENCY = "NZD"


def missing_environment(prod: bool = PROD) -> list[str]:
    if not prod:
        return [] if DATABASE_URL else ["COLLECTOR_DB_URL"]
    required = {"DBX_SERVER_HOSTNAME": DBX_SERVER_HOSTNAME, "DBX_HTTP_PATH": DBX_HTTP_PATH}
    return sorted(name for name, value in required.items() if not value)
