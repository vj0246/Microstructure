"""Milestone gates. Each check returns report lines; a line starting with "FAIL" fails the gate."""

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from impact.analysis.estimators import primary_cell
from impact.clean import adjustment, panel
from impact.ingest.nse_client import DAILY
from impact.ingest.readers import (
    deal_year_files,
    file_day,
    read_all,
    read_deals,
    read_legacy_bhav,
    read_mto,
    read_sec_full,
    read_udiff_bhav,
)


def nse_start(source: str) -> date:
    return DAILY[source].start

RAJVANSHI_2010_2019 = 81_506  # NSE bulk-deal records 2010-2019, Rajvanshi (2021), IIM Calcutta WP 863
DESIGN_PULL_2010_2019 = 76_784  # same years pulled with csv=true during the design review, 2026-09-13
LIVE_DAYS_REQUIRED = 5
GATED_DEALS = ("bulk_deals", "block_deals")
SMALL_SOURCES = ("deals", "events", "master", "live")


def m0(raw: Path) -> list[str]:
    return _deal_halves(raw) + _bulk_anchor(raw) + _live(raw) + _events(raw) + _manifest(raw)


def m1(raw: Path, out: Path) -> list[str]:
    """Clean-panel gates (PLAN M1) on the build output and on the raw overlap periods."""
    return _panel_gates(out) + _bhav_overlap(raw) + _delivery_overlap(raw) + _residual_jumps(out)


def _panel_gates(out: Path) -> list[str]:
    prices = pl.scan_parquet(out / "prices" / "*.parquet").select("symbol", "date", "series", "close", "prev_close").collect()
    sessions = missing_sessions(prices)
    lines = [
        f"{'ok  ' if not sessions else 'FAIL'} missing weekend sessions (fetch with `impact fetch daily --dates`): "
        + (" ".join(str(d) for d in sessions) or "none")
    ]
    first, last = prices["date"].min(), prices["date"].max()
    audit = pl.read_parquet(out / "factor_audit.parquet").filter(
        pl.col("date").is_between(first, last) & pl.col("trade_date").is_not_null()
    )
    text = audit.filter(pl.col("source") == "subject")
    testable = pl.col("factor").log().abs() >= adjustment.AMBIGUOUS_LOG
    tested = text.filter(testable)
    confirmed = tested["confirmed"].mean() or 0.0
    lines.append(
        f"{'ok  ' if confirmed >= 0.98 else 'FAIL'} split/bonus factors confirmed by the ex-date jump: "
        f"{tested['confirmed'].sum()}/{tested.height} ({confirmed:.1%}); "
        f"{text.filter(~testable).height} too small to test, applied as dated; "
        f"{audit.filter((pl.col('source') == 'jump') & pl.col('apply')).height} arrangement/demerger gaps applied"
    )
    grid = pl.scan_parquet(out / "panel" / "*.parquet").select("symbol", "date", "volume", "cum", "last").collect()
    lo, hi = grid["date"].min(), grid["date"].max()
    clients = pl.read_parquet(out / "client_days.parquet").filter(pl.col("date").is_between(lo, hi))
    joined = clients.join(grid, on=["symbol", "date"], how="left")
    in_scope = joined.filter(~pl.col("out_of_scope"))
    priced = in_scope["last"].is_not_null().mean() or 0.0
    lines.append(
        f"{'ok  ' if priced >= 0.995 else 'FAIL'} in-scope client-days priced on their date: {priced:.2%} "
        f"({joined.height - in_scope.height} out of scope: SME, DR, fund units)"
    )
    normal = joined.filter(~pl.col("block_window") & (pl.col("volume") > 0))
    over = normal.filter(pl.col("net").abs() * pl.col("cum") > pl.col("volume"))
    lines.append(
        f"{'ok  ' if over.height == 0 else 'FAIL'} net Q/V <= 1 outside block-window days: {over.height} of "
        f"{normal.height} client-days exceed, e.g. {over.select('symbol', 'date', 'net', 'volume').head(3).rows()}"
    )
    return lines


def m2(raw: Path, out: Path) -> list[str]:
    """Census (PLAN D2), the band-flag validation and the M2 power gate. Counts and pooled statistics
    only: no returns by type."""
    return _census(out) + _band_coverage(raw, out)


def _band_coverage(raw: Path, out: Path) -> list[str]:
    """M2 gate. On 218 sampled days the price rule alone reached 99.4% recall but 47.2% precision against
    NSE's band-hit list (it also flags 2% and 5% moves of wider-band stocks), so band flags now require
    the list (PLAN J6 as amended). A PR zip must therefore exist for at least 99% of trading days."""
    have = {file_day(p) for p in (raw / "pr").glob("*/*.zip")}
    calendar = pl.scan_parquet(out / "prices" / "*.parquet").select("date").unique().collect()["date"]
    days = [d for d in calendar.to_list() if d >= nse_start("pr")]
    share = sum(d in have for d in days) / len(days)
    return [f"{'ok  ' if share >= 0.99 else 'FAIL'} NSE band-hit lists present for {share:.1%} of {len(days)} trading days"]


def _census(out: Path) -> list[str]:
    table = pl.read_parquet(out / "event_table.parquet")
    lines = [
        "info events per year: "
        + " ".join(f"{y}:{n}" for y, n in table.group_by(pl.col("date").dt.year()).len().sort("date").rows()),
        "info event-day shares: "
        + ", ".join(
            f"{name} {table.select(expr.mean()).item():.1%}"
            for name, expr in (
                ("isolated", pl.col("isolated")),
                ("news", pl.col("news")),
                ("g1 censored (open at band)", pl.col("open_band_next") != 0),
                ("LAST at band", pl.col("last_band") != 0),
                ("any cross", pl.col("crossed") > 0),
            )
        ),
    ]
    clients = pl.read_parquet(out / "client_days.parquet").filter(~pl.col("out_of_scope"))
    lines.append(f"info churner client-days: {(clients['ctype'] == 'P').mean():.1%} of in-scope client-days")
    logx = table.filter(pl.col("x") > 0).with_columns(lx=pl.col("x").log10())
    spread = logx.group_by("symbol").agg(sd=pl.col("lx").std(), n=pl.len()).filter(pl.col("n") >= 5)["sd"].median()
    lines.append(
        f"info log10 x: 5-95% span {logx['lx'].quantile(0.95) - logx['lx'].quantile(0.05):.2f} decades pooled, "
        f"median within-stock sd {spread:.2f}"
    )
    lines.append(f"info overnight share of daily variance (median stock): {_overnight_share(out):.2f}")
    lines.append("info 5-day same-side continuation by type: " + _continuation(clients, out))
    counts = dict(primary_cell(table).group_by("stock_type").len().rows())
    ok = min(counts.get("I", 0), counts.get("R", 0)) >= 1000
    lines.append(
        f"{'ok  ' if ok else 'FAIL'} primary cell before common support: I {counts.get('I', 0)}, R {counts.get('R', 0)} "
        "(need 1,000 each; else the primary turns descriptive and the headline is D1 plus S2)"
    )
    return lines


def _overnight_share(out: Path) -> float:
    shares = []
    for chunk in sorted((out / "panel").glob("*.parquet")):
        legs = pl.read_parquet(chunk, columns=["symbol", "g1", "id"]).drop_nulls()
        per = legs.group_by("symbol").agg(n=pl.len(), g1=pl.col("g1").var(), id=pl.col("id").var())
        shares.append(per.filter(pl.col("n") >= 250).select(pl.col("g1") / (pl.col("g1") + pl.col("id"))).to_series())
    return float(pl.concat(shares).median())


def _continuation(clients: pl.DataFrame, out: Path) -> str:
    """Share of directional client-days followed by a same-side disclosure by the same client in the
    same stock within 5 trading days (descriptive; S1 uses a point-in-time version)."""
    calendar = panel.market_calendar(pl.scan_parquet(out / "prices" / "*.parquet").select("date").unique().collect()["date"])
    c = clients.filter(pl.col("directional")).join(calendar, on="date").sort("client_norm", "symbol", "day")
    ahead = c.with_columns(
        cont=((pl.col("day").shift(-1) - pl.col("day")) <= 5).over("client_norm", "symbol")
        & (pl.col("net").sign().shift(-1) == pl.col("net").sign()).over("client_norm", "symbol")
    )
    rates = ahead.group_by("ctype").agg(pl.col("cont").fill_null(False).mean()).sort("ctype").rows()
    return ", ".join(f"{t} {r:.1%}" for t, r in rates)


AUDIT_SIZE = 200
# One seed per audit round; each later round draws only names no earlier round has seen. Round
# 20260914 scored 90.0%, round 20260915 92.0%, round 20260916 94.5% and round 20260917 93.5%; each
# trained rule fixes, so the gate is judged on the latest round. Round 20260918 is the last: the
# misses had become a long tail of one-off names, and its score is reported as measured.
AUDIT_SEED = 20260918


def m4(out: Path) -> list[str]:
    """PLAN M4 gates: delivery on churner-dominated days, then the typing audit."""
    return _churner_delivery(out) + _typing_audit(out)


def _churner_delivery(out: Path) -> list[str]:
    """Round trips need no delivery, so stock-days whose disclosed gross is at least 80% churner flow should
    show a delivery share (delivered / traded quantity) below the median of the other disclosed stock-days
    in the same benchmark bucket and year."""
    clients = pl.read_parquet(out / "client_days.parquet", columns=["date", "symbol", "ctype", "gross"])
    churn = clients.group_by("date", "symbol").agg(
        churn=(pl.col("gross") * (pl.col("ctype") == "P")).sum() / pl.col("gross").sum()
    )
    days = (
        pl.read_parquet(out / "event_table.parquet", columns=["date", "symbol", "bucket", "deliv_qty", "volume"])
        .join(churn, on=["date", "symbol"])
        .filter(pl.col("deliv_qty").is_not_null() & (pl.col("volume") > 0) & pl.col("bucket").is_not_null())
        .with_columns(
            share=pl.col("deliv_qty") / pl.col("volume"), dominated=pl.col("churn") >= 0.8, year=pl.col("date").dt.year()
        )
    )
    median = days.filter(~pl.col("dominated")).group_by("bucket", "year").agg(med=pl.col("share").median())
    gap = days.filter(pl.col("dominated")).join(median, on=["bucket", "year"])
    if gap.is_empty():
        return ["FAIL delivery on churner-dominated days: no such stock-days with delivery data"]
    diff = (gap["share"] - gap["med"]).median()
    below = (gap["share"] < gap["med"]).mean()
    return [
        f"{'ok  ' if diff < 0 else 'FAIL'} delivery on {gap.height} churner-dominated stock-days: median gap to "
        f"bucket-year median {diff:+.1%}, {below:.0%} below it"
    ]


def _typing_audit(out: Path) -> list[str]:
    """Typing audit (PLAN M4). The first run of a round writes typing_audit_<seed>.csv: a random sample of
    names behind in-scope directional client-days, excluding names audited in earlier rounds, with the
    assigned type and an empty `verdict` column. Once every verdict holds the correct type (I, R, P or
    S), assigned types must agree on at least 95% of names."""
    sheet = out / f"typing_audit_{AUDIT_SEED}.csv"
    if not sheet.exists():
        earlier = [pl.read_csv(p, infer_schema=False)["client_norm"] for p in out.glob("typing_audit_*.csv")]
        seen = pl.concat(earlier) if earlier else pl.Series("client_norm", [], pl.String)
        clients = pl.read_parquet(out / "client_days.parquet").filter(
            ~pl.col("out_of_scope") & pl.col("directional") & ~pl.col("client_norm").is_in(seen.implode())
        )
        names = clients.group_by("client_norm").agg(
            client=pl.col("client").first(),
            assigned=pl.col("ctype").mode().first(),
            subtype=pl.col("subtype").mode().first(),
            client_days=pl.len(),
            value_cr=((pl.col("buy_value") + pl.col("sell_value")).sum() / 1e7).round(2),
            securities=pl.col("security").unique().head(3).str.join(" | "),
        )
        sample = names.sample(n=min(AUDIT_SIZE, names.height), seed=AUDIT_SEED).sort("assigned", "client_norm")
        sample.with_columns(verdict=pl.lit(""), note=pl.lit("")).write_csv(sheet)
        return [f"PENDING typing audit sheet written to {sheet}: fill `verdict` (I, R, P or S) on every row, then rerun"]
    audit = pl.read_csv(sheet, infer_schema=False).with_columns(pl.col("verdict").fill_null("").str.strip_chars().str.to_uppercase())
    filled = audit.filter(pl.col("verdict").is_in(["I", "R", "P", "S"]))
    if filled.height < audit.height:
        return [f"PENDING typing audit: {filled.height}/{audit.height} verdicts filled in {sheet}"]
    agreement = (filled["verdict"] == filled["assigned"]).mean()
    misses = filled.filter(pl.col("verdict") != pl.col("assigned")).select("client_norm", "assigned", "verdict").head(10).rows()
    return [f"{'ok  ' if agreement >= 0.95 else 'FAIL'} typing audit agreement {agreement:.1%} on {filled.height} names; misses e.g. {misses}"]


def m3(out: Path) -> list[str]:
    """Rajvanshi (2021) replication on pooled gross bulk-deal records, 2010-2019 (PLAN M3): abnormal
    close-to-close returns (stock minus the cross-sectional median) on days -7..+7 around buy and sell
    records. Pooled only: nothing here splits by trader type."""
    deals = pl.read_parquet(out / "deals.parquet", columns=["symbol", "date", "side"]).filter(
        pl.col("date").is_between(date(2010, 1, 1), date(2019, 12, 31))
    )
    grid = (
        pl.scan_parquet(out / "panel" / "*.parquet")
        .select("symbol", "date", "day", "close")
        .filter(pl.col("date") <= date(2020, 1, 31))
        .collect()
        .sort("symbol", "day")
        .with_columns(r=(pl.col("close") / pl.col("close").shift(1)).log().over("symbol"))
    )
    abnormal = grid.with_columns(ar=pl.col("r") - pl.col("r").median().over("day")).select("symbol", "day", "ar")
    located = deals.join(grid.select("symbol", "date", "day"), on=["symbol", "date"])
    path = pl.concat(
        [
            located.with_columns(pl.col("day") + k)
            .join(abnormal, on=["symbol", "day"])
            .group_by("side")
            .agg(offset=pl.lit(k), mean=pl.col("ar").mean(), se=pl.col("ar").std() / pl.len().sqrt())
            for k in range(-7, 8)
        ]
    ).sort("side", "offset")
    buys = path.filter(pl.col("side") == "BUY")["mean"].to_list()
    sells = path.filter(pl.col("side") == "SELL")["mean"].to_list()
    peak = max(range(15), key=lambda i: buys[i]) == 7
    reverts = sum(buys[8:]) < 0
    # Rajvanshi reports buy-side returns above sell-side ones, not the sell sign: gross sell records are
    # mostly same-day round trips, so their sign is reported but not gated.
    ordered = buys[7] > 0 and buys[7] > sells[7]
    return [
        f"info records located: {located.height}; buy AR by day -7..+7 (%): " + " ".join(f"{100 * v:.2f}" for v in buys),
        "info sell AR by day -7..+7 (%): " + " ".join(f"{100 * v:.2f}" for v in sells),
        f"{'ok  ' if ordered else 'FAIL'} day-0 AR positive for buys ({100 * buys[7]:.2f}%) and above sells ({100 * sells[7]:.2f}%)",
        f"{'ok  ' if peak and reverts else 'FAIL'} buy AR peaks on day 0 and reverses over days +1..+7",
    ]


_KEY = ["date", "symbol", "series"]
_EQUITY = pl.col("series").is_in(panel.EQUITY_SERIES)


def _bhav_overlap(raw: Path) -> list[str]:
    """Legacy and UDiFF bhavcopy must agree exactly where both exist (2024-01-01 to 2024-07-05)."""
    legacy = sorted((raw / "legacy_bhav" / "2024").glob("*.zip"))
    udiff = [p for p in sorted((raw / "udiff_bhav" / "2024").glob("*.zip")) if file_day(p) <= date(2024, 7, 5)]
    if not legacy or not udiff:
        return ["PENDING legacy vs UDiFF overlap: 2024 files not downloaded yet"]
    a = read_all(legacy, read_legacy_bhav).filter(_EQUITY).with_columns(in_a=pl.lit(True))
    b = read_all(udiff, read_udiff_bhav).filter(_EQUITY).with_columns(in_b=pl.lit(True))
    joined = a.join(b, on=_KEY, how="full", suffix="_u", coalesce=True)
    both = joined.filter(pl.col("in_a") & pl.col("in_b"))
    cols = ("open", "high", "low", "close", "last", "prev_close", "volume")
    same = pl.all_horizontal([(pl.col(c) - pl.col(f"{c}_u")).abs() <= 1e-6 for c in cols]).fill_null(False)
    diff = both.filter(~same)
    return [
        f"{'ok  ' if diff.height == 0 else 'FAIL'} legacy vs UDiFF on {both['date'].n_unique()} overlap days: "
        f"{diff.height} of {both.height} rows differ, {joined.height - both.height} rows in one file only, "
        f"e.g. {diff.select(_KEY).head(3).rows()}"
    ]


def _delivery_overlap(raw: Path, every: int = 20) -> list[str]:
    """On every `every`-th day that all three exist (sampled to bound memory): MTO and sec_bhavdata_full
    delivery must agree, and legacy VWAP (value / volume) must sit within 0.1% of AVG_PRICE or within its
    2-decimal rounding (0.005), which dominates for penny stocks."""
    sec = {file_day(p): p for p in (raw / "sec_full").glob("*/*.csv")}
    mto = {file_day(p): p for p in (raw / "mto").glob("*/*.DAT")}
    legacy = {file_day(p): p for p in (raw / "legacy_bhav").glob("*/*.zip")}
    days = sorted(sec.keys() & mto.keys() & legacy.keys())[::every]
    if not days:
        return ["PENDING delivery and VWAP overlap: sec_bhavdata_full/MTO files not downloaded yet"]
    s = read_all([sec[d] for d in days], read_sec_full).filter(_EQUITY)
    m = read_all([mto[d] for d in days], read_mto).filter(_EQUITY)
    bhav = read_all([legacy[d] for d in days], read_legacy_bhav).filter(_EQUITY & (pl.col("volume") > 0))
    delivery = s.join(m, on=_KEY, suffix="_m")
    unequal = delivery.filter((pl.col("deliv_qty") != pl.col("deliv_qty_m")) | (pl.col("volume") != pl.col("volume_m")))
    vwap = s.join(bhav, on=_KEY, suffix="_b").select(
        close_enough=((pl.col("value") / pl.col("volume_b") - pl.col("avg_price")).abs()
                      <= pl.max_horizontal(0.001 * pl.col("avg_price"), pl.lit(0.005 + 1e-9)))
    )["close_enough"].mean() or 0.0
    return [
        f"{'ok  ' if unequal.height == 0 else 'FAIL'} MTO vs sec_bhavdata_full delivery on {len(days)} sampled days: "
        f"{unequal.height} of {delivery.height} rows differ, e.g. {unequal.select(_KEY).head(3).rows()}",
        f"{'ok  ' if vwap >= 0.99 else 'FAIL'} legacy VWAP within 0.1% (or rounding) of AVG_PRICE: {vwap:.2%} of rows",
    ]


def _residual_jumps(out: Path, limit: float = 0.405) -> list[str]:
    """Adjusted last-to-last moves above 50% (|log| > 0.405): leftover corporate actions or bad prints."""
    jumps = []
    for chunk in sorted((out / "panel").glob("*.parquet")):
        g = pl.read_parquet(chunk, columns=["symbol", "date", "last"])
        g = g.with_columns(r=(pl.col("last") / pl.col("last").forward_fill().shift(1).over("symbol")).log())
        jumps.append(g.filter(pl.col("r").abs() > limit))
    found = pl.concat(jumps).sort(pl.col("r").abs(), descending=True)
    return [f"info adjusted day-to-day moves above 50%: {found.height}, largest {found.head(5).rows()}"]


def missing_sessions(prices: pl.DataFrame) -> list[date]:
    """Weekend days just before trading days on which most symbols' PREVCLOSE differs from their last
    close in the data: sessions (Muhurat, special Saturdays) that the weekday-only pull skipped."""
    rows = panel.equity_rows(prices).sort("symbol", "date").with_columns(pc=pl.col("close").shift(1).over("symbol"))
    off = rows.drop_nulls("pc").group_by("date").agg(share=((pl.col("pc") / pl.col("prev_close") - 1).abs() > 0.001).mean())
    market = sorted(prices["date"].unique())
    candidates = []
    for day in off.filter(pl.col("share") > 0.5)["date"].sort():
        prior = market[market.index(day) - 1]
        span = (prior + timedelta(days=k) for k in range(1, (day - prior).days))
        candidates += [d for d in span if d.weekday() >= 5]
    return candidates


def _window_files(folder: Path) -> dict[tuple[date, date], Path]:
    """Files named <kind>_<start>_<end>.csv keyed by their (start, end) window."""
    out: dict[tuple[date, date], Path] = {}
    for path in folder.glob("*.csv"):
        start, end = path.stem.rsplit("_", 2)[1:]
        out[date.fromisoformat(start), date.fromisoformat(end)] = path
    return out


def _rows(path: Path) -> int:
    return pl.read_csv(path, infer_schema=False).height


def year_rows(folder: Path) -> dict[int, int]:
    """Rows per calendar year, from the widest window that starts on 1 January."""
    return {year: _rows(path) for year, path in deal_year_files(folder).items()}


def _deal_halves(raw: Path) -> list[str]:
    lines = []
    for folder in sorted(p for p in (raw / "deals").glob("*") if p.is_dir()):
        files = _window_files(folder)
        for (start, end), path in sorted(files.items()):
            if (start.month, start.day) != (1, 1) or end.month < 7:
                continue  # only windows reaching into H2 have two halves to compare
            h1 = files.get((start, date(start.year, 6, 30)))
            h2 = files.get((date(start.year, 7, 1), end))
            if h1 is None or h2 is None:
                lines.append(f"FAIL {folder.name} {start.year}: half-year windows missing")
                continue
            full, halves = _rows(path), _rows(h1) + _rows(h2)
            failure = "FAIL" if folder.name in GATED_DEALS else "info"  # short selling is not used in v1
            lines.append(f"{'ok  ' if full == halves else failure} {folder.name} {start.year}: {full} rows, halves {halves}")
    return lines or ["FAIL deals: nothing downloaded"]


def _bulk_anchor(raw: Path) -> list[str]:
    rows = year_rows(raw / "deals" / "bulk_deals")
    total = sum(n for year, n in rows.items() if 2010 <= year <= 2019)
    gap = total / RAJVANSHI_2010_2019 - 1
    ok = total == DESIGN_PULL_2010_2019 and abs(gap) <= 0.10
    return [
        f"{'ok  ' if ok else 'FAIL'} bulk rows 2010-2019: {total} "
        f"(design pull {DESIGN_PULL_2010_2019}, Rajvanshi {RAJVANSHI_2010_2019}, gap {gap:+.1%})"
    ]


def _live(raw: Path) -> list[str]:
    key = ["symbol", "client", "side", "qty", "price"]
    results: dict[date, bool] = {}
    lines = []
    for snap in sorted((raw / "live").glob("bulk_snapshot_*.csv")):
        live = read_deals(snap)
        day = live["date"].max()
        api = raw / "live" / "bulk_deals" / f"bulk_deals_{day}_{day}.csv"
        if not api.exists():
            lines.append(f"FAIL live {day}: API pull missing")
            continue
        pulled = read_deals(api)
        results[day] = live.select(key).sort(key).equals(pulled.select(key).sort(key))
        lines.append(f"{'ok  ' if results[day] else 'FAIL'} live {day}: snapshot {live.height} rows, API {pulled.height}")
    matched = sum(results.values())
    status = "ok  " if matched >= LIVE_DAYS_REQUIRED else "PEND"
    lines.append(f"{status} live days matched: {matched}/{LIVE_DAYS_REQUIRED} (run `impact fetch live` on separate trading days)")
    return lines


def _records(path: Path) -> list[dict[str, object]]:
    data = json.loads(path.read_bytes())
    return data["data"] if isinstance(data, dict) else data


def _events(raw: Path) -> list[str]:
    lines = []
    for folder in sorted(p for p in (raw / "events").glob("*") if p.is_dir()):
        counts = [len(_records(p)) for p in sorted(folder.glob("*.json"))]
        lines.append(
            f"info {folder.name}: {len(counts)} months, {sum(counts)} records, "
            f"max/month {max(counts, default=0)}, empty months {counts.count(0)}"
        )
    pit = next((r for p in sorted((raw / "events" / "pit").glob("*.json")) for r in _records(p)), None)
    has_dates = pit is not None and {"acqfromDt", "acqtoDt"} <= pit.keys()
    lines.append(f"{'ok  ' if has_dates else 'FAIL'} PIT records carry acquisition from/to dates")
    return lines


def _manifest(raw: Path) -> list[str]:
    """Re-hash every small-source file against its manifest record (daily price files are skipped)."""
    manifest = raw / "manifest.jsonl"
    if not manifest.exists():
        return ["FAIL manifest missing"]
    checked = bad = 0
    with manifest.open(encoding="utf-8") as fh:
        for rec in map(json.loads, fh):
            rel = rec.get("path")
            if rel is None or rel.split("/")[0] not in SMALL_SOURCES:
                continue
            checked += 1
            path = raw / rel
            bad += not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != rec["sha256"]
    return [f"{'ok  ' if bad == 0 else 'FAIL'} manifest: {checked} files re-hashed, {bad} missing or changed"]
