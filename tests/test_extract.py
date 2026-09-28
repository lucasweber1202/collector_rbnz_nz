"""RBNZ workbook contract: IDs, layout, blanks, stitching, access failures."""
from __future__ import annotations

import os
from datetime import date, timedelta

import httpx
import pytest

from scripts import metadata
from scripts.extract import (
    SourceAccessError,
    SourceData,
    SourceLayoutError,
    build_series_id,
    check_payload,
    collect,
    file_url,
    filter_usable_series,
    merge_files,
    parse_series_id,
    parse_workbook,
)
from tests.rbnz_workbook import OCR_PUBLISHED, b1, b2

OCR = "RBNZ_B2_INM_DP1_N"
TWI = "RBNZ_B1_EXRT_DS41_NZB17"


def test_ids_are_the_audited_native_codes() -> None:
    assert build_series_id("INM.DP1.N") == OCR
    assert build_series_id("EXRT.DS41.NZB17") == TWI
    assert parse_series_id(OCR) == "INM.DP1.N"
    with pytest.raises(ValueError):
        build_series_id("INM.DG102.NZZCF")  # published, but not selected
    with pytest.raises(ValueError):
        parse_series_id("RBNZ_B2_INM_DP1")


def test_official_file_urls() -> None:
    assert file_url("B2", "hb2-daily-close") == "https://www.rbnz.govt.nz/-/media/project/sites/rbnz/files/statistics/series/b/b2/hb2-daily-close.xlsx"
    assert file_url("B1", "hb1-daily-1999-2017").endswith("/b/b1/hb1-daily-1999-2017.xlsx")


def test_ocr_is_located_by_series_id_and_matches_the_published_values() -> None:
    data = parse_workbook(b2(), "B2", "https://www.rbnz.govt.nz/x.xlsx")
    assert {(o.reference_date, o.value) for o in data.observations} == set(OCR_PUBLISHED.items())
    entry = data.catalog[OCR]
    assert (entry["unit"], entry["frequency"], entry["eco_group"], entry["country"]) == ("percent", "daily", "interest_rates", "NZD")
    assert entry["last_publish_date"] == date(2026, 9, 15)
    assert "source unit %pa" in entry["description"]
    metadata.validate_catalog(data.catalog)


def test_blank_cells_are_absent_not_zero() -> None:
    values = {date(2020, 5, 14): 0.25, date(2020, 5, 15): 0.25}
    blob = b2(values)
    data = parse_workbook(blob, "B2", "u")
    assert all(o.value == 0.25 for o in data.observations)


def test_twi_parses_with_its_source_unit_recorded() -> None:
    data = parse_workbook(b1({date(2026, 9, 14): 68.9}), "B1", "u")
    assert [(o.series_id, o.value) for o in data.observations] == [(TWI, 68.9)]
    assert data.catalog[TWI]["unit"] == "index" and "source unit Index" in data.catalog[TWI]["description"]


def test_layout_and_value_drift_fail() -> None:
    with pytest.raises(SourceLayoutError, match="exactly once"):
        parse_workbook(b1({date(2026, 9, 14): 68.9}), "B2", "u")
    with pytest.raises(ValueError, match="Implausible"):
        parse_workbook(b2({date(2026, 9, 14): 275.0}), "B2", "u")
    import io

    import openpyxl

    book = openpyxl.load_workbook(io.BytesIO(b2()))
    book["Data"]["A5"] = "Series"
    out = io.BytesIO()
    book.save(out)
    with pytest.raises(SourceLayoutError, match="header"):
        parse_workbook(out.getvalue(), "B2", "u")


def test_history_and_current_files_stitch_and_conflicts_fail() -> None:
    history = parse_workbook(b2({date(2017, 12, 29): 1.75}), "B2", "hist")
    current = parse_workbook(b2({date(2018, 1, 3): 1.75}), "B2", "curr")
    merged = merge_files([history, current])
    assert [o.reference_date for o in merged.observations] == [date(2017, 12, 29), date(2018, 1, 3)]
    assert merged.catalog[OCR]["source_url"] == "curr"
    clash = parse_workbook(b2({date(2017, 12, 29): 2.0}), "B2", "curr")
    with pytest.raises(SourceLayoutError, match="disagree"):
        merge_files([history, clash])


def test_stale_or_short_series_fail() -> None:
    today = date(2026, 9, 27)
    long = {today - timedelta(days=d): 2.75 for d in range(0, 6 * 366, 7)}
    data = parse_workbook(b2(long), "B2", "u")
    assert filter_usable_series(data, today) is data
    with pytest.raises(SourceLayoutError, match="stale"):
        filter_usable_series(data, today + timedelta(days=30))
    short = parse_workbook(b2({date(2026, 9, 11): 2.75, date(2026, 9, 14): 2.75}), "B2", "u")
    with pytest.raises(SourceLayoutError, match="short"):
        filter_usable_series(SourceData(short.observations, short.catalog), date(2026, 9, 15))


CHALLENGE = b'<!DOCTYPE html><html lang="en"><head><title>Just a moment...</title>'


def test_cloudflare_challenge_fails_deterministically_with_the_remedy() -> None:
    response = httpx.Response(403, content=CHALLENGE, headers={"content-type": "text/html; charset=UTF-8", "cf-mitigated": "challenge"}, request=httpx.Request("GET", "https://www.rbnz.govt.nz/x.xlsx"))
    with pytest.raises(SourceAccessError, match="allowlisting"):
        check_payload(response)
    ok_but_html = httpx.Response(200, content=b"<html>Website unavailable</html>", headers={"content-type": "text/html"}, request=httpx.Request("GET", "https://www.rbnz.govt.nz/x.xlsx"))
    with pytest.raises(SourceAccessError):
        check_payload(ok_but_html)
    not_xlsx = httpx.Response(200, content=b"x" * 10000, headers={"content-type": "application/octet-stream"}, request=httpx.Request("GET", "https://www.rbnz.govt.nz/x.xlsx"))
    with pytest.raises(SourceAccessError, match="not an XLSX"):
        check_payload(not_xlsx)
    blob = b2()
    fine = httpx.Response(200, content=blob, headers={"content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}, request=httpx.Request("GET", "https://www.rbnz.govt.nz/x.xlsx"))
    assert check_payload(fine) == blob


def test_collect_stops_on_the_first_challenge(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(403, content=CHALLENGE, headers={"cf-mitigated": "challenge", "content-type": "text/html"})

    monkeypatch.setattr("scripts.extract.DOWNLOAD_DELAY", 0.0)
    real_client = httpx.Client
    monkeypatch.setattr("scripts.extract.httpx.Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    with pytest.raises(SourceAccessError, match="Cloudflare challenge"):
        collect()
    assert len(calls) == 1


@pytest.mark.skipif(os.getenv("RBNZ_LIVE_SMOKE") != "1", reason="opt-in; needs an RBNZ-allowlisted IP")
def test_live_official_workbooks() -> None:
    result = collect()
    ocr = {o.reference_date: o.value for o in result.observations if o.series_id == OCR}
    assert ocr[date(2026, 9, 14)] == 2.75 and ocr[date(2020, 5, 15)] == 0.25
    assert min(ocr) == date(1999, 3, 17)
    assert {e.name for e in result.releases} == {"B1", "B2"}
