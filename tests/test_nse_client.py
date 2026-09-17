import hashlib
import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from impact.config import Settings
from impact.ingest import nse_client


class FakeTime:
    """Clock that only advances when the code under test sleeps."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def clock(self) -> float:
        return self.now


def make_client(handler, tmp_path: Path, fake: FakeTime) -> nse_client.NseClient:
    settings = Settings(data_dir=tmp_path, max_retries=3)
    return nse_client.NseClient(settings, transport=httpx.MockTransport(handler), sleep=fake.sleep, clock=fake.clock)


def test_retries_server_errors_with_backoff(tmp_path):
    codes = iter([503, 500, 200])
    fake = FakeTime()
    with make_client(lambda req: httpx.Response(next(codes), content=b"ok"), tmp_path, fake) as c:
        assert c.get(f"{nse_client.ARCHIVES}/x.csv") == b"ok"
    assert fake.sleeps == [2.0, 4.0]


def test_404_returns_none_without_retry(tmp_path):
    fake = FakeTime()
    with make_client(lambda req: httpx.Response(404), tmp_path, fake) as c:
        assert c.get(f"{nse_client.ARCHIVES}/holiday.zip") is None
    assert fake.sleeps == []


def test_client_error_is_not_retried(tmp_path):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(400)

    with make_client(handler, tmp_path, FakeTime()) as c, pytest.raises(nse_client.NseError, match="HTTP 400"):
        c.get(f"{nse_client.ARCHIVES}/bad")
    assert len(calls) == 1


def test_api_bootstraps_cookie_and_refreshes_it_after_403(tmp_path):
    seen = []
    api_codes = iter([403, 200])

    def handler(req):
        seen.append(req.url.path)
        if req.url.path.startswith("/report-detail"):
            return httpx.Response(200)
        return httpx.Response(next(api_codes), content=b"[]")

    with make_client(handler, tmp_path, FakeTime()) as c:
        assert c.get(f"{nse_client.SITE}/api/corporates-pit", api=True) == b"[]"
    assert seen == ["/report-detail/display-bulk-and-block-deals", "/api/corporates-pit"] * 2


def test_requests_are_spaced_by_the_interval(tmp_path):
    fake = FakeTime()
    with make_client(lambda req: httpx.Response(200, content=b"x"), tmp_path, fake) as c:
        c.get(f"{nse_client.ARCHIVES}/a")
        c.get(f"{nse_client.ARCHIVES}/b")
    assert fake.sleeps == [1.0]


def test_store_is_write_once_and_logs_sha256(tmp_path):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, content=b"PKzipbytes")

    store = nse_client.RawStore(tmp_path / "raw")
    with make_client(handler, tmp_path, FakeTime()) as c:
        first = store.fetch(c, "pr/2012/PR020112.zip", f"{nse_client.ARCHIVES}/pr.zip", nse_client.is_zip)
        second = store.fetch(c, "pr/2012/PR020112.zip", f"{nse_client.ARCHIVES}/pr.zip", nse_client.is_zip)
    assert first == second and first.read_bytes() == b"PKzipbytes"
    assert len(calls) == 1
    (record,) = [json.loads(line) for line in store.manifest.read_text().splitlines()]
    assert record["path"] == "pr/2012/PR020112.zip"
    assert record["sha256"] == hashlib.sha256(b"PKzipbytes").hexdigest()


def test_store_caches_404_across_instances(tmp_path):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(404)

    url = f"{nse_client.ARCHIVES}/holiday.zip"
    with make_client(handler, tmp_path, FakeTime()) as c:
        assert nse_client.RawStore(tmp_path / "raw").fetch(c, "x/holiday.zip", url, nse_client.is_zip) is None
        assert nse_client.RawStore(tmp_path / "raw").fetch(c, "x/holiday.zip", url, nse_client.is_zip) is None
    assert len(calls) == 1


def test_store_rejects_block_page_served_with_200(tmp_path):
    store = nse_client.RawStore(tmp_path / "raw")
    with make_client(lambda req: httpx.Response(200, content=b"<html>denied</html>"), tmp_path, FakeTime()) as c:
        with pytest.raises(nse_client.NseError, match="unexpected content"):
            store.fetch(c, "pr/x.zip", f"{nse_client.ARCHIVES}/x.zip", nse_client.is_zip)
    assert not (tmp_path / "raw" / "pr" / "x.zip").exists()


def test_text_check_rejects_workbooks_and_binary():
    assert nse_client.is_text(b"SYMBOL, SERIES\nABC, EQ\n")
    assert not nse_client.is_text(b"PK\x03\x04[Content_Types].xml")
    assert not nse_client.is_text(b"SYMBOL\x00\x00")
    assert not nse_client.is_text(b"  <html>blocked</html>")


def test_daily_fetch_skips_a_bad_day_and_keeps_going(tmp_path, capsys):
    def handler(req):
        body = b"PK\x03\x04workbook" if "02012012" in req.url.path else b"Record Type\n20,1,ABC,EQ,10,5,50\n"
        return httpx.Response(200, content=body)

    store = nse_client.RawStore(tmp_path / "raw")
    with make_client(handler, tmp_path, FakeTime()) as c:
        present = nse_client.fetch_days(c, store, "mto", [date(2012, 1, 2), date(2012, 1, 3)])
    assert present == 1
    assert "FAILED mto 2012-01-02" in capsys.readouterr().out
    assert not (tmp_path / "raw" / "mto" / "2012" / "MTO_02012012.DAT").exists()


def test_urls_match_files_verified_on_nse():
    assert nse_client.DAILY["legacy_bhav"].url(date(2012, 1, 2)) == (
        "https://nsearchives.nseindia.com/content/historical/EQUITIES/2012/JAN/cm02JAN2012bhav.csv.zip"
    )
    assert nse_client.DAILY["udiff_bhav"].url(date(2024, 7, 8)) == (
        "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_20240708_F_0000.csv.zip"
    )
    assert nse_client.DAILY["sec_full"].url(date(2024, 7, 8)).endswith("/products/content/sec_bhavdata_full_08072024.csv")
    assert nse_client.DAILY["mto"].url(date(2012, 1, 2)).endswith("/archives/equities/mto/MTO_02012012.DAT")
    assert nse_client.DAILY["pr"].url(date(2012, 1, 2)).endswith("/archives/equities/bhavcopy/pr/PR020112.zip")
    assert nse_client.DAILY["sec_list"].url(date(2020, 1, 2)).endswith("/content/equities/sec_list_02012020.csv")
    assert nse_client.deals_url("bulk_deals", date(2024, 1, 1), date(2024, 1, 31)) == (
        "https://www.nseindia.com/api/historicalOR/bulk-block-short-deals"
        "?optionType=bulk_deals&from=01-01-2024&to=31-01-2024&csv=true"
    )


def test_windows():
    assert nse_client.deal_windows(2010, date(2026, 9, 12)) == [
        (date(2010, 1, 1), date(2010, 12, 31)),
        (date(2010, 1, 1), date(2010, 6, 30)),
        (date(2010, 7, 1), date(2010, 12, 31)),
    ]
    assert nse_client.deal_windows(2026, date(2026, 9, 12))[0] == (date(2026, 1, 1), date(2026, 9, 12))
    assert list(nse_client.month_windows(date(2016, 1, 1), date(2016, 3, 15))) == [
        (date(2016, 1, 1), date(2016, 1, 31)),
        (date(2016, 2, 1), date(2016, 2, 29)),
    ]
    assert list(nse_client.weekdays(date(2026, 9, 11), date(2026, 9, 14))) == [date(2026, 9, 11), date(2026, 9, 14)]
