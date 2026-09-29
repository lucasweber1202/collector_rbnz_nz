"""Official RBNZ statistical workbooks: Official Cash Rate and the TWI.

Two published tables, each split by RBNZ into a history workbook and a
current workbook:

* B2 wholesale interest rates, daily close -- ``INM.DP1.N`` Official Cash Rate;
* B1 exchange rates and TWI, daily -- ``EXRT.DS41.NZB17`` Trade Weighted Index
  (17-currency basket).

Every RBNZ series workbook has the same layout: a ``Data`` sheet whose first
five rows are group, series, notes, ``Unit`` and ``Series Id``, followed by one
row per date; a ``Series Definitions`` sheet (Group, Series, Series Id, Unit,
Note); and a ``Table Description`` sheet carrying ``Published Date``. Columns
are located by ``Series Id``, never by position. A blank cell is a published
date with no value and is absent, never zero.
"""

from __future__ import annotations

import hashlib
import io
import logging
import math
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import httpx
import openpyxl

from scripts.config import COUNTRY_CURRENCY, DOWNLOAD_DELAY, REQUEST_TIMEOUT, USER_AGENT
from scripts.releases import ReleaseEvidence
from scripts.time_series import Observation

logger = logging.getLogger(__name__)
FILE_ROOT = "https://www.rbnz.govt.nz/-/media/project/sites/rbnz/files/statistics/series/b"
TERMS_URL = "https://www.rbnz.govt.nz/about-our-site/terms-of-use"
PAGES = {
    "B1": "https://www.rbnz.govt.nz/statistics/series/exchange-and-interest-rates/exchange-rates-and-the-trade-weighted-index",
    "B2": "https://www.rbnz.govt.nz/statistics/series/exchange-and-interest-rates/wholesale-interest-rates",
}
# History workbook first, current workbook last (RBNZ's own split).
FILES = {
    "B1": ("hb1-daily-1999-2017", "hb1-daily"),
    "B2": ("hb2-daily-close-1985-2017", "hb2-daily-close"),
}


@dataclass(frozen=True)
class Selected:
    table: str
    name: str
    unit: str
    eco_group: str
    source_units: frozenset[str] | None  # None: any published unit string, kept in the description
    plausible: tuple[float, float]


SELECTED = {
    "INM.DP1.N": Selected(
        "B2",
        "Official Cash Rate (OCR)",
        "percent",
        "interest_rates",
        frozenset({"%pa"}),
        (0.0, 25.0),
    ),
    "EXRT.DS41.NZB17": Selected(
        "B1",
        "Trade Weighted Index (TWI), 17-currency basket",
        "index",
        "exchange_rates",
        None,
        (20.0, 200.0),
    ),
}
DATA_SHEET = "Data"
UNIT_ROW = 3  # zero-based: row 4 in the workbook
ID_ROW = 4  # zero-based: row 5 in the workbook
MIN_PAYLOAD_BYTES = 5_000
MIN_HISTORY_YEARS = 5
MAX_STALE_DAYS = 14
CHALLENGE_MARKERS = (
    b"just a moment",
    b"cf-mitigated",
    b"challenge-platform",
    b"website unavailable",
    b"captcha",
    b"incapsula",
    b"access denied",
)


class SourceLayoutError(ValueError):
    """An RBNZ workbook no longer has the audited layout."""


class SourceAccessError(RuntimeError):
    """RBNZ answered with a challenge or error page instead of a workbook."""


@dataclass(frozen=True)
class SourceData:
    observations: list[Observation]
    catalog: dict[str, dict[str, Any]]
    releases: tuple[ReleaseEvidence, ...] = ()


def build_series_id(native: str) -> str:
    """``RBNZ_<table>_<native with . → _>``; only the audited native IDs."""
    if native not in SELECTED:
        raise ValueError(f"Not a selected RBNZ series: {native}")
    return f"RBNZ_{SELECTED[native].table}_{native.replace('.', '_')}"


def parse_series_id(series_id: str) -> str:
    for native in SELECTED:
        if build_series_id(native) == series_id:
            return native
    raise ValueError(f"Invalid RBNZ series_id: {series_id}")


def file_url(table: str, name: str) -> str:
    return f"{FILE_ROOT}/{table.lower()}/{name}.xlsx"


def check_payload(response: httpx.Response) -> bytes:
    """Refuse a Cloudflare challenge, an error page or a non-workbook body."""
    blob = response.content
    lowered = blob[:4096].lower()
    content_type = response.headers.get("content-type", "").lower()
    challenged = response.headers.get("cf-mitigated") == "challenge" or any(
        m in lowered for m in CHALLENGE_MARKERS
    )
    if (
        challenged
        or "text/html" in content_type
        or lowered.lstrip().startswith((b"<!doctype", b"<html"))
    ):
        raise SourceAccessError(
            f"RBNZ answered HTTP {response.status_code} with a {'Cloudflare challenge' if challenged else 'HTML page'} "
            f"for {response.url}. RBNZ grants automated access by allowlisting the caller's static public IP; "
            f"request it from RBNZ (contact on {TERMS_URL}) for the machine that runs this collector."
        )
    response.raise_for_status()
    if not blob.startswith(b"PK\x03\x04"):
        raise SourceAccessError(f"RBNZ file is not an XLSX workbook: {response.url}")
    if len(blob) < MIN_PAYLOAD_BYTES:
        raise SourceAccessError(f"RBNZ workbook is implausibly small ({len(blob)} bytes)")
    return blob


def _date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def published_date(workbook: Any) -> date:
    """The workbook's own ``Published Date`` from its ``Table Description``."""
    if "Table Description" not in workbook:
        raise SourceLayoutError("RBNZ Table Description sheet missing")
    for row in workbook["Table Description"].values:
        if row and row[0] == "Published Date":
            published = _date(row[1])
            if published is None:
                raise SourceLayoutError(f"RBNZ Published Date is not a date: {row[1]!r}")
            return published
    raise SourceLayoutError("RBNZ Published Date missing")


def parse_workbook(blob: bytes, table: str, url: str) -> SourceData:
    """Parse the selected columns of one RBNZ workbook, located by Series Id."""
    workbook = openpyxl.load_workbook(io.BytesIO(blob), read_only=True, data_only=True)
    if DATA_SHEET not in workbook or "Series Definitions" not in workbook:
        raise SourceLayoutError(f"RBNZ {table} sheets changed: {workbook.sheetnames}")
    published = published_date(workbook)
    rows = list(workbook[DATA_SHEET].values)
    if len(rows) <= ID_ROW or rows[UNIT_ROW][0] != "Unit" or rows[ID_ROW][0] != "Series Id":
        raise SourceLayoutError(f"RBNZ {table} Data header changed")
    ids = list(rows[ID_ROW])
    definitions = {
        r[2]: r for r in list(workbook["Series Definitions"].values)[1:] if r and len(r) > 3
    }
    wanted = [native for native, spec in SELECTED.items() if spec.table == table]
    catalog: dict[str, dict[str, Any]] = {}
    columns: dict[int, str] = {}
    for native in wanted:
        if ids.count(native) != 1:
            raise SourceLayoutError(
                f"RBNZ {table} must list {native} exactly once (found {ids.count(native)})"
            )
        column = ids.index(native)
        spec = SELECTED[native]
        unit = str(rows[UNIT_ROW][column] or "").strip()
        if not unit or (spec.source_units is not None and unit not in spec.source_units):
            raise SourceLayoutError(f"RBNZ {native} unit changed: {unit!r}")
        definition = definitions.get(native)
        if definition is None or str(definition[3] or "").strip() != unit:
            raise SourceLayoutError(f"RBNZ Series Definitions disagree for {native}")
        sid = build_series_id(native)
        catalog[sid] = {
            "name": spec.name,
            "description": f"RBNZ {table}: {definition[0]} / {definition[1]}; source unit {unit}; RBNZ series {native}",
            "country": COUNTRY_CURRENCY,
            "frequency": "daily",
            "unit": spec.unit,
            "eco_group": spec.eco_group,
            "source_url": url,
            "last_publish_date": published,
        }
        columns[column] = native
    snapshot = hashlib.sha256(blob).hexdigest()
    observations: list[Observation] = []
    seen: set[tuple[str, date]] = set()
    for row in rows[ID_ROW + 1 :]:
        when = _date(row[0]) if row else None
        if when is None:
            continue
        for column, native in columns.items():
            raw = row[column] if column < len(row) else None
            if raw is None or raw == "":
                continue
            low, high = SELECTED[native].plausible
            if not isinstance(raw, int | float) or not math.isfinite(raw) or not low <= raw <= high:
                raise ValueError(f"Implausible RBNZ value {raw!r} for {native} on {when}")
            sid = build_series_id(native)
            if (sid, when) in seen:
                raise ValueError(f"Duplicate RBNZ date {when} for {native}")
            seen.add((sid, when))
            observations.append(Observation(sid, when, float(raw), snapshot))
    return SourceData(observations, catalog)


def merge_files(parts: list[SourceData]) -> SourceData:
    """Stitch history and current workbooks of one table.

    RBNZ splits a series at the end of 2017, so the files should not overlap.
    If a date appears in both, the values must agree; a conflict means one file
    was revised without the other and the run stops for a re-audit. Metadata
    (URL, publication date, unit) is taken from the current workbook.
    """
    values: dict[tuple[str, date], Observation] = {}
    for part in parts:
        for obs in part.observations:
            key = (obs.series_id, obs.reference_date)
            existing = values.get(key)
            if existing is not None and abs(existing.value - obs.value) > 1e-9:
                raise SourceLayoutError(
                    f"RBNZ history and current files disagree at {key}: {existing.value} vs {obs.value}"
                )
            values[key] = obs
    catalog: dict[str, dict[str, Any]] = {}
    for part in parts:
        catalog.update(part.catalog)
    return SourceData(
        sorted(values.values(), key=lambda o: (o.series_id, o.reference_date)), catalog
    )


def filter_usable_series(data: SourceData, today: date) -> SourceData:
    """Every selected series must be current and long; otherwise re-audit."""
    for sid in data.catalog:
        points = [o.reference_date for o in data.observations if o.series_id == sid]
        if not points:
            raise SourceLayoutError(f"RBNZ {sid} has no observations")
        stale = (today - max(points)).days
        history = (max(points) - min(points)).days / 365.25
        if stale > MAX_STALE_DAYS or history < MIN_HISTORY_YEARS:
            raise SourceLayoutError(
                f"RBNZ {sid} is stale ({stale} days) or short ({history:.1f} years)"
            )
    return data


def collect() -> SourceData:
    """Download every audited workbook, pausing between requests as RBNZ asks."""
    tables: dict[str, SourceData] = {}
    with httpx.Client(
        timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT}, follow_redirects=True
    ) as client:
        first = True
        for table, names in FILES.items():
            parts = []
            for name in names:
                if not first:
                    time.sleep(DOWNLOAD_DELAY)
                first = False
                url = file_url(table, name)
                parts.append(parse_workbook(check_payload(client.get(url)), table, url))
            tables[table] = merge_files(parts)
    merged = SourceData(
        [o for part in tables.values() for o in part.observations],
        {sid: entry for part in tables.values() for sid, entry in part.catalog.items()},
    )
    usable = filter_usable_series(merged, datetime.now(UTC).date())
    evidence = tuple(
        ReleaseEvidence(
            table,
            file_url(table, FILES[table][-1]),
            max(entry["last_publish_date"] for entry in part.catalog.values()),
            max(o.reference_date for o in part.observations),
            frozenset(part.catalog),
        )
        for table, part in tables.items()
    )
    return SourceData(usable.observations, usable.catalog, evidence)
