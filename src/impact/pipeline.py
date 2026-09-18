"""Raw store to analysis-ready parquet. Memory-bounded, because the full daily panel does not fit in
RAM: prices by year, then deals, typed flows and scope flags, then the panel in symbol chunks on one
market calendar, then same-date benchmark cuts and means year by year."""

from collections.abc import Callable
from pathlib import Path

import polars as pl

from impact.clean import adjustment, panel, symbols
from impact.flows import disclosures, trader_types
from impact.ingest import nse_client, readers

START = nse_client.DAILY["legacy_bhav"].start
UDIFF_FROM = nse_client.DAILY["udiff_bhav"].start
CHUNK_SYMBOLS = 200
PRICE_INPUT = ["symbol", "date", "series", "isin", "open", "high", "low", "close", "last", "prev_close", "volume", "value"]
WINDOW_SERIES = ["BL", "IL"]  # block and institutional windows


def build(raw: Path, out: Path) -> None:
    breaks = symbols.canonical_breaks(symbols.read_symbol_changes(_latest(raw / "master", "symbolchange_*.csv")))
    prices = pl.scan_parquet(_prices_by_year(raw, out / "prices") / "*.parquet").select(PRICE_INPUT)
    flags = _scope_flags(prices, breaks)
    flagged, clients, days = _flows(raw, breaks, flags)
    for name, frame in (("deals", flagged), ("client_days", clients), ("stock_days", days)):
        frame.write_parquet(out / f"{name}.parquet")
    actions = readers.read_corporate_actions(sorted((raw / "events" / "corporate_actions").glob("*.json")))
    actions = symbols.relabel(actions.rename({"ex_date": "date"}), breaks)
    calendar = panel.market_calendar(prices.select("date").unique().collect()["date"])
    hits = _band_hits(raw, breaks)
    chunks, audit = _panel_chunks(prices, calendar, days, actions, hits, breaks, flags["fund_units"], out / "panel")
    audit.write_parquet(out / "factor_audit.parquet")
    _benchmarks(chunks, out)
    _event_table(raw, breaks, clients, days, calendar, actions, chunks, out)


def _latest(folder: Path, pattern: str) -> Path:
    snapshots = sorted(folder.glob(pattern))  # names carry the snapshot date
    if not snapshots:
        raise FileNotFoundError(f"no {pattern} in {folder}")
    return snapshots[-1]


def _prices_by_year(raw: Path, out: Path) -> Path:
    """prices/<year>.parquet: legacy bhavcopy before 2024, UDiFF from 2024 (M1 checks their overlap)."""
    out.mkdir(parents=True, exist_ok=True)
    sources = [(raw / "legacy_bhav", readers.read_legacy_bhav), (raw / "udiff_bhav", readers.read_udiff_bhav)]
    for folder, reader in sources:
        for year_dir in sorted(p for p in folder.glob("*") if p.is_dir()):
            if (int(year_dir.name) >= UDIFF_FROM.year) == (reader is readers.read_udiff_bhav):
                readers.read_all(sorted(year_dir.glob("*.zip")), reader).write_parquet(out / f"{year_dir.name}.parquet")
    return out


def _scope_flags(prices: pl.LazyFrame, breaks: pl.DataFrame) -> dict[str, pl.DataFrame]:
    """Canonical stock-days traded in the block/institutional windows, stock-days traded only outside
    ordinary equity series (SME, depository receipts, ...), and symbols that are fund units (INF ISIN:
    ETFs and other mutual-fund units)."""

    def days(condition: pl.Expr) -> pl.DataFrame:
        return symbols.relabel(prices.filter(condition).select("symbol", "date").unique().collect(), breaks).unique()

    return {
        "window_days": days(pl.col("series").is_in(WINDOW_SERIES)),
        "other_days": days(~pl.col("series").is_in([*panel.EQUITY_SERIES, *WINDOW_SERIES])),
        "fund_units": days(pl.col("isin").str.starts_with("INF")).select("symbol").unique(),
    }


def _flows(  # `dictionary`: None uses the full hand dictionary; the sensitivity run passes variants
    raw: Path, breaks: pl.DataFrame, flags: dict[str, pl.DataFrame], dictionary: pl.DataFrame | None = None
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Bulk deals from the price start (earlier disclosures still exclude days from ADV and sigma
    windows), block legs netted out, exact crosses flagged; typed client-days; stock-day flows with
    their type, `block_window` and `out_of_scope` flags."""

    def deals(kind: str) -> pl.DataFrame:
        frame = readers.read_all(readers.deal_year_files(raw / "deals" / kind).values(), readers.read_deals)
        return symbols.relabel(disclosures.with_client_norm(frame.filter(pl.col("date") >= START)), breaks)

    def mark(frame: pl.DataFrame) -> pl.DataFrame:
        key = ["symbol", "date"]
        return (
            frame.join(flags["window_days"].with_columns(block_window=pl.lit(True)), on=key, how="left")
            .join(flags["other_days"].with_columns(other_series=pl.lit(True)), on=key, how="left")
            .with_columns(
                pl.col("block_window").fill_null(False),
                out_of_scope=pl.col("other_series").fill_null(False)
                | pl.col("symbol").is_in(flags["fund_units"]["symbol"].implode()),
            )
            .drop("other_series")
        )

    pit = readers.read_pit(sorted((raw / "events" / "pit").glob("*.json"))).drop_nulls("date")
    insiders = pit.filter(pl.col("category").str.contains("(?i)promoter|director|kmp|key managerial|relative"))
    insiders = symbols.relabel(insiders, breaks).select("symbol", client_norm=disclosures.normalize_client(pl.col("name")))
    flagged = disclosures.flag_crosses(disclosures.net_out_block(deals("bulk_deals"), deals("block_deals")))
    if dictionary is None:
        dictionary = trader_types.read_dictionary()
    clients = trader_types.classify(disclosures.client_days(flagged), insiders, dictionary)
    days = disclosures.stock_days(clients).join(disclosures.stock_day_types(clients), on=["date", "symbol"], how="left")
    return flagged, mark(clients), mark(days)


def _band_hits(raw: Path, breaks: pl.DataFrame) -> pl.DataFrame:
    """NSE's band-hit lists from every PR zip, equity series, canonical symbols: (symbol, date, side)."""
    files = sorted((raw / "pr").glob("*/*.zip"))
    if not files:
        return pl.DataFrame(schema={"symbol": pl.String, "date": pl.Date, "side": pl.Int8})
    hits = readers.read_all(files, readers.read_band_hits).filter(pl.col("series").is_in(panel.EQUITY_SERIES))
    return symbols.relabel(hits, breaks).select("symbol", "date", "side")


def _panel_chunks(
    prices: pl.LazyFrame,
    calendar: pl.DataFrame,
    days: pl.DataFrame,
    actions: pl.DataFrame,
    hits: pl.DataFrame,
    breaks: pl.DataFrame,
    fund_units: pl.DataFrame,
    out: Path,
) -> tuple[list[Path], pl.DataFrame]:
    """Panel chunk files (fund units excluded) plus the factor audit (every candidate factor, applied or
    not). `actions`: corporate actions with canonical symbol and `date` = ex-date."""
    out.mkdir(parents=True, exist_ok=True)
    factors = adjustment.action_factors(actions)
    keys = prices.filter(pl.col("series").is_in(panel.EQUITY_SERIES)).select("symbol", "date").unique().collect()
    members = (
        symbols.to_canonical(keys, breaks)
        .filter(~pl.col("canonical").is_in(fund_units["symbol"].implode()))
        .group_by("canonical")
        .agg(pl.col("symbol").unique())
        .sort("canonical")
    )
    disclosed = days.select("symbol", "date", disclosed=pl.lit(True))
    paths, audits = [], []
    for i, chunk in enumerate(members.iter_slices(CHUNK_SYMBOLS)):
        in_chunk = pl.col("symbol").is_in(chunk["canonical"].implode())
        raw_symbols = chunk["symbol"].explode().unique().implode()
        rows = symbols.relabel(panel.equity_rows(prices.filter(pl.col("symbol").is_in(raw_symbols)).collect()), breaks)
        rows = rows.filter(in_chunk).drop("isin")
        duplicated = rows.filter(pl.struct("symbol", "date").is_duplicated())
        if duplicated.height:
            raise ValueError(f"symbol chains collide on {duplicated.height} rows, e.g. {duplicated.head(3).rows()}")
        known = factors.filter(in_chunk)
        audit = pl.concat(
            [adjustment.confirm(rows, known), adjustment.arrangement_factors(rows, actions.filter(in_chunk), known)]
        )
        applied = (
            audit.filter("apply")
            .group_by("symbol", pl.col("trade_date").alias("date"))
            .agg(pl.col("factor").product())
        )
        grid = adjustment.apply_factors(panel.band_flags(panel.on_calendar(rows, calendar), hits.filter(in_chunk)), applied)
        grid = (
            panel.legs(grid)
            .join(disclosed, on=["symbol", "date"], how="left")
            .with_columns(
                pl.col("disclosed").fill_null(False),
                prev_last_raw=(pl.col("last") * pl.col("cum")).shift(1).over("symbol"),  # price filter, unadjusted
            )
        )
        path = out / f"chunk_{i:03d}.parquet"
        panel.mark_eligible(panel.windows(grid)).write_parquet(path)
        paths.append(path)
        audits.append(audit)
    return paths, pl.concat(audits)


def _benchmarks(chunks: list[Path], out: Path) -> None:
    """bench_cuts.parquet and bench_means.parquet, one year at a time; event rows join them later."""
    lazy = pl.scan_parquet(chunks).filter(panel.USABLE).select("date", "eligible", "med_value", "sigma", *panel.LEGS)
    years = lazy.select(pl.col("date").dt.year().unique()).collect().to_series().sort()
    cuts, means = [], []
    for year in years:
        rows = lazy.filter(pl.col("date").dt.year() == year).collect()
        year_cuts = panel.bucket_cuts(rows)
        cuts.append(year_cuts)
        means.append(panel.bucket_means(panel.add_buckets(rows, year_cuts)))
    pl.concat(cuts).sort("date").write_parquet(out / "bench_cuts.parquet")
    pl.concat(means).sort("date", "bucket").write_parquet(out / "bench_means.parquet")


def _delivery(raw: Path, breaks: pl.DataFrame, keys: pl.DataFrame) -> pl.DataFrame:
    """Delivered quantity (unadjusted shares) per canonical stock-day in `keys`, equity series only: MTO
    until sec_bhavdata_full takes over in Oct 2019, sec_bhavdata_full after. Read a year at a time."""
    switch = nse_client.DAILY["sec_full"].start
    sources = [
        (raw / "mto", "*.DAT", readers.read_mto, lambda day: day < switch),
        (raw / "sec_full", "*.csv", readers.read_sec_full, lambda day: day >= switch),
    ]
    parts = []
    for folder, pattern, reader, keep in sources:
        for year_dir in sorted(p for p in folder.glob("*") if p.is_dir()):
            files = [p for p in sorted(year_dir.glob(pattern)) if keep(readers.file_day(p))]
            if not files:
                continue
            year = (
                readers.read_all(files, reader)
                .filter(pl.col("series").is_in(panel.EQUITY_SERIES))
                .group_by("symbol", "date")
                .agg(deliv_qty=pl.when(pl.col("deliv_qty").is_not_null().any()).then(pl.col("deliv_qty").sum()))
            )
            parts.append(symbols.relabel(year, breaks).join(keys, on=["symbol", "date"], how="semi"))
    if not parts:
        return pl.DataFrame(schema={"symbol": pl.String, "date": pl.Date, "deliv_qty": pl.Int64})
    return pl.concat(parts)


def _event_table(
    raw: Path,
    breaks: pl.DataFrame,
    clients: pl.DataFrame,
    days: pl.DataFrame,
    calendar: pl.DataFrame,
    actions: pl.DataFrame,
    chunks: list[Path],
    out: Path,
) -> None:
    """event_table.parquet: in-scope stock-days with net disclosed flow outside the block windows, with
    type, isolation, novelty and news flags, panel features, bucket, abnormal legs and x = |net| / ADV
    (both in latest share units). cross_table.parquet: the same for stock-days with exact crosses, with
    buyer and seller types and x = crossed / ADV. Filters are flags; the estimation cells apply them."""

    def dated(kind: str, reader: Callable[[list[Path]], pl.DataFrame]) -> pl.DataFrame:
        return reader(sorted((raw / "events" / kind).glob("*.json")))

    board = symbols.relabel(dated("board_meetings", readers.read_board_meetings), breaks)
    results = pl.concat([dated(k, readers.read_results) for k in ("results_quarterly", "results_annual")]).unique()
    results = symbols.relabel(results.with_columns(date=pl.col("broadcast").dt.date()), breaks).drop("date")
    flags = disclosures.event_flags(clients, days, calendar).join(
        disclosures.news_flags(days, calendar, board, results, actions), on=["date", "symbol"]
    )
    in_scope = days.filter(~pl.col("out_of_scope") & ~pl.col("block_window"))
    candidates = in_scope.filter(pl.col("net") != 0)
    crosses = in_scope.filter(pl.col("crossed") > 0).join(disclosures.cross_types(clients), on=["date", "symbol"])
    keys = pl.concat([candidates.select("symbol", "date"), crosses.select("symbol", "date")]).unique()
    rows = pl.scan_parquet(chunks).join(keys.lazy(), on=["symbol", "date"], how="semi").collect()
    rows = panel.abnormal(
        panel.add_buckets(rows, pl.read_parquet(out / "bench_cuts.parquet")), pl.read_parquet(out / "bench_means.parquet")
    ).drop("disclosed")

    delivered = _delivery(raw, breaks, keys)

    def assemble(frame: pl.DataFrame, flow: pl.Expr) -> pl.DataFrame:
        return (
            frame.join(flags, on=["date", "symbol"], how="left")
            .join(rows, on=["symbol", "date"], how="inner")
            .join(delivered, on=["symbol", "date"], how="left")
            .with_columns(x=flow * pl.col("cum") / pl.col("adv"))
            .sort("date", "symbol")
        )

    assemble(candidates, pl.col("net").abs()).write_parquet(out / "event_table.parquet")
    assemble(crosses, pl.col("crossed")).write_parquet(out / "cross_table.parquet")
