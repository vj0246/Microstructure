"""NSE downloads: a polite HTTP client and a write-once raw store with a sha256 manifest.

Archive files (nsearchives.nseindia.com) need only browser headers. The www.nseindia.com/api
endpoints also need the cookies an ordinary report page sets; they are re-fetched on 401/403.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx

from impact.config import Settings

SITE = "https://www.nseindia.com"
ARCHIVES = "https://nsearchives.nseindia.com"
COOKIE_PAGE = f"{SITE}/report-detail/display-bulk-and-block-deals"
RETRY_STATUS = frozenset({401, 403, 429, 500, 502, 503, 504})
MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
DEAL_KINDS = ("bulk_deals", "block_deals", "short_selling")
MASTER_FILES = ("symbolchange.csv", "namechange.csv", "EQUITY_L.csv")


class NseError(RuntimeError):
    """A request failed for good, or returned something other than the expected file."""


class NseClient:
    """Browser-header client: minimum spacing between requests, cookie bootstrap for API calls,
    exponential backoff on throttling, auth and server errors. A 404 means "no such file"."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._sleep = sleep
        self._clock = clock
        self._last_request = float("-inf")
        self._has_cookie = False
        self._http = httpx.Client(
            headers={
                "User-Agent": settings.user_agent,
                "Referer": f"{SITE}/",
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=settings.timeout_s,
            follow_redirects=True,
            transport=transport,
        )

    def __enter__(self) -> NseClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._http.close()

    def get(self, url: str, *, api: bool = False) -> bytes | None:
        """Body of `url`, or None when the server says the file does not exist."""
        problem = ""
        for attempt in range(self._settings.max_retries + 1):
            if attempt:
                self._sleep(min(60.0, 2.0**attempt))
            try:
                if api and not self._has_cookie:
                    self._has_cookie = self._send(COOKIE_PAGE).status_code == 200
                    if not self._has_cookie:
                        problem = "cookie page refused"
                        continue
                resp = self._send(url)
            except httpx.TransportError as exc:
                problem = f"{type(exc).__name__}: {exc}"
                continue
            if resp.status_code == 200:
                return resp.content
            if resp.status_code == 404:
                return None
            problem = f"HTTP {resp.status_code}"
            if resp.status_code in (401, 403):
                self._has_cookie = False
            if resp.status_code not in RETRY_STATUS:
                break
        raise NseError(f"{url}: {problem}")

    def _send(self, url: str) -> httpx.Response:
        wait = self._last_request + self._settings.request_interval_s - self._clock()
        if wait > 0:
            self._sleep(wait)
        self._last_request = self._clock()
        return self._http.get(url)


class RawStore:
    """Write-once files under `root`. Every download, and every confirmed 404, is appended to
    root/manifest.jsonl, so reruns skip finished work and each file's sha256 is on record."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.manifest = root / "manifest.jsonl"
        self._missing: set[str] = set()
        if self.manifest.exists():
            with self.manifest.open(encoding="utf-8") as fh:
                self._missing = {r["url"] for r in map(json.loads, fh) if r.get("status") == 404}

    def fetch(
        self,
        client: NseClient,
        rel: str,
        url: str,
        valid: Callable[[bytes], bool],
        *,
        api: bool = False,
    ) -> Path | None:
        """Download `url` to root/rel unless already stored; None if the file does not exist."""
        path = self.root / rel
        if path.exists():
            return path
        if url in self._missing:
            return None
        body = client.get(url, api=api)
        if body is None:
            self._missing.add(url)
            self._log({"url": url, "status": 404})
            return None
        if not valid(body):
            raise NseError(f"{url}: unexpected content {body[:80]!r}")
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        part.write_bytes(body)
        os.replace(part, path)
        self._log({"path": rel, "url": url, "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)})
        return path

    def _log(self, record: dict[str, object]) -> None:
        record["fetched_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        self.root.mkdir(parents=True, exist_ok=True)
        with self.manifest.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")


def is_zip(body: bytes) -> bool:
    return body.startswith(b"PK")


def is_json(body: bytes) -> bool:
    return body.lstrip()[:1] in (b"{", b"[")


def is_deals_csv(body: bytes) -> bool:
    return body.removeprefix(b"\xef\xbb\xbf").lstrip(b'"').startswith(b"Date")


def is_text(body: bytes) -> bool:
    """Plain CSV/DAT table: rejects the HTML page NSE serves when it blocks a request, and binary bodies
    such as the Excel workbook it once served as sec_bhavdata_full_08082022.csv."""
    return bool(body.strip()) and not body.lstrip().startswith((b"<", b"PK")) and b"\x00" not in body[:4096]


@dataclass(frozen=True)
class DailySource:
    start: date
    end: date | None
    url: Callable[[date], str]
    valid: Callable[[bytes], bool]


def _legacy_bhav_url(day: date) -> str:
    mon = MONTHS[day.month - 1]
    return f"{ARCHIVES}/content/historical/EQUITIES/{day.year}/{mon}/cm{day:%d}{mon}{day.year}bhav.csv.zip"


# Price files start in Sep 2009 to leave ~80 trading days of ADV/sigma history before 2010.
DAILY: dict[str, DailySource] = {
    "legacy_bhav": DailySource(date(2009, 9, 1), date(2024, 7, 5), _legacy_bhav_url, is_zip),
    "udiff_bhav": DailySource(
        date(2024, 1, 1),
        None,
        lambda d: f"{ARCHIVES}/content/cm/BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip",
        is_zip,
    ),
    "sec_full": DailySource(
        date(2019, 10, 1), None, lambda d: f"{ARCHIVES}/products/content/sec_bhavdata_full_{d:%d%m%Y}.csv", is_text
    ),
    # sec_bhavdata_full carries delivery from Oct 2019; MTO runs to mid-2020 to leave an overlap for the M1 gate.
    "mto": DailySource(
        date(2009, 9, 1), date(2020, 6, 30), lambda d: f"{ARCHIVES}/archives/equities/mto/MTO_{d:%d%m%Y}.DAT", is_text
    ),
    "pr": DailySource(date(2010, 1, 1), None, lambda d: f"{ARCHIVES}/archives/equities/bhavcopy/pr/PR{d:%d%m%y}.zip", is_zip),
    "band_changes": DailySource(
        date(2010, 1, 1), None, lambda d: f"{ARCHIVES}/content/equities/eq_band_changes_{d:%d%m%Y}.csv", is_text
    ),
    "sec_list": DailySource(date(2020, 1, 1), None, lambda d: f"{ARCHIVES}/content/equities/sec_list_{d:%d%m%Y}.csv", is_text),
}

# Monthly JSON windows; the results API returns records only for period=Quarterly and period=Annual.
# PIT comes first: its endpoint already returns nothing from 2026-06, so it is the data most at risk.
_API = f"{SITE}/api"
EVENTS: dict[str, tuple[date, str]] = {
    "pit": (date(2016, 1, 1), _API + "/corporates-pit?index=equities&from_date={a}&to_date={b}"),
    "corporate_actions": (date(2005, 1, 1), _API + "/corporates-corporateActions?index=equities&from_date={a}&to_date={b}"),
    "board_meetings": (date(2008, 1, 1), _API + "/corporate-board-meetings?index=equities&from_date={a}&to_date={b}"),
    "results_quarterly": (
        date(2012, 1, 1),
        _API + "/corporates-financial-results?index=equities&from_date={a}&to_date={b}&period=Quarterly",
    ),
    "results_annual": (
        date(2012, 1, 1),
        _API + "/corporates-financial-results?index=equities&from_date={a}&to_date={b}&period=Annual",
    ),
}


def weekdays(start: date, end: date) -> Iterator[date]:
    day = start
    while day <= end:
        if day.weekday() < 5:
            yield day
        day += timedelta(days=1)


def month_windows(start: date, end: date) -> Iterator[tuple[date, date]]:
    """Complete calendar months from start's month through the last month ending on or before `end`."""
    first = start.replace(day=1)
    while True:
        following = date(first.year + first.month // 12, first.month % 12 + 1, 1)
        last = following - timedelta(days=1)
        if last > end:
            return
        yield first, last
        first = following


def deal_windows(year: int, until: date) -> list[tuple[date, date]]:
    """Full year plus both halves, clipped to `until`; M0 compares them to catch silent truncation."""
    spans = [
        (date(year, 1, 1), date(year, 12, 31)),
        (date(year, 1, 1), date(year, 6, 30)),
        (date(year, 7, 1), date(year, 12, 31)),
    ]
    return [(a, min(b, until)) for a, b in spans if a <= until]


def deals_url(kind: str, start: date, end: date) -> str:
    return (
        f"{SITE}/api/historicalOR/bulk-block-short-deals?optionType={kind}"
        f"&from={start:%d-%m-%Y}&to={end:%d-%m-%Y}&csv=true"
    )


def fetch_deal_window(
    client: NseClient, store: RawStore, kind: str, start: date, end: date, folder: str = "deals"
) -> Path | None:
    rel = f"{folder}/{kind}/{kind}_{start}_{end}.csv"
    return store.fetch(client, rel, deals_url(kind, start, end), is_deals_csv, api=True)


def fetch_events(client: NseClient, store: RawStore, name: str, until: date) -> None:
    start, template = EVENTS[name]
    for a, b in month_windows(start, until):
        url = template.format(a=f"{a:%d-%m-%Y}", b=f"{b:%d-%m-%Y}")
        store.fetch(client, f"events/{name}/{name}_{a:%Y-%m}.json", url, is_json, api=True)


def fetch_master(client: NseClient, store: RawStore, today: date) -> None:
    """Dated snapshots: NSE overwrites these files in place."""
    for name in MASTER_FILES:
        stem, ext = name.rsplit(".", 1)
        store.fetch(client, f"master/{stem}_{today}.{ext}", f"{ARCHIVES}/content/equities/{name}", is_text)


def fetch_live_snapshot(client: NseClient, store: RawStore, today: date) -> Path | None:
    url = f"{ARCHIVES}/content/equities/bulk.csv"
    return store.fetch(client, f"live/bulk_snapshot_{today}.csv", url, is_deals_csv)


def fetch_days(client: NseClient, store: RawStore, name: str, days: Iterable[date]) -> int:
    """Download one daily source for the given days within its coverage; returns files present.
    Holidays come back as 404 and are cached as missing. A day that fails for good is reported and
    skipped (nothing is stored for it, so a rerun retries it)."""
    src = DAILY[name]
    present = 0
    for day in days:
        if src.start <= day <= (src.end or day):
            url = src.url(day)
            try:
                if store.fetch(client, f"{name}/{day.year}/{url.rsplit('/', 1)[1]}", url, src.valid):
                    present += 1
            except NseError as exc:
                print(f"FAILED {name} {day}: {exc}", flush=True)
    return present


def fetch_daily(client: NseClient, store: RawStore, name: str, start: date, end: date) -> int:
    """Weekdays over [start, end]; weekend sessions (Muhurat, special Saturdays) need explicit days."""
    return fetch_days(client, store, name, weekdays(start, end))
