"""Daily equity price panel on the market calendar.

Pipeline (PLAN section 5): equity_rows -> symbols.relabel -> on_calendar -> band_flags (raw prices)
-> adjustment.apply_factors -> legs -> windows -> mark_eligible -> benchmark (bucket_cuts, add_buckets,
bucket_means, abnormal). Windows count market trading days; a day without trades has zero volume
and null prices."""

import math

import polars as pl

EQUITY_SERIES = ("EQ", "BE", "BZ")  # priority order; SME series (SM, ST) are out of scope
BANDS = (0.02, 0.05, 0.10, 0.20)
TICK = 0.05
CARRY_DAYS = 20  # keep delisted or suspended names on the grid so decay legs carry the last price
GK_COEF = 2 * math.log(2) - 1
LEGS = ("ov", "id", "g1", "g1c", "d15", "d620", "r_m1", "r_m5")  # g1c: official-close baseline (robustness 1)
USABLE = pl.col("eligible") & pl.col("med_value").is_not_null() & pl.col("sigma").is_not_null()
_VALUE_CUTS = (0.2, 0.4, 0.6, 0.8)
_SIGMA_CUTS = (1 / 3, 2 / 3)


def equity_rows(prices: pl.DataFrame) -> pl.DataFrame:
    """One row per (symbol, date) from the EQ, BE and BZ series, preferring EQ."""
    rank = pl.col("series").replace_strict(
        {s: i for i, s in enumerate(EQUITY_SERIES)}, default=None, return_dtype=pl.Int8
    )
    return (
        prices.with_columns(_rank=rank)
        .drop_nulls("_rank")
        .sort("symbol", "date", "_rank")
        .unique(["symbol", "date"], keep="first", maintain_order=True)
        .drop("_rank")
    )


def market_calendar(dates: pl.Series) -> pl.DataFrame:
    """Every date with any trade, numbered by `day` (market trading-day index)."""
    return pl.DataFrame({"date": dates.unique().sort()}).with_row_index("day").with_columns(pl.col("day").cast(pl.Int64))


def on_calendar(panel: pl.DataFrame, calendar: pl.DataFrame) -> pl.DataFrame:
    """Reindex each symbol to every market day from its first row to CARRY_DAYS after its last.

    Adds `day` and `age` (trading days since the symbol's first row)."""
    end = calendar.height - 1
    spans = panel.join(calendar, on="date").group_by("symbol").agg(first=pl.col("day").min(), last=pl.col("day").max())
    grid = spans.select(
        "symbol",
        "first",
        day=pl.int_ranges("first", pl.min_horizontal(pl.col("last") + CARRY_DAYS, pl.lit(end)) + 1),
    ).explode("day", empty_as_null=False)  # ranges are never empty: first <= last
    return (
        grid.join(calendar, on="day")
        .join(panel, on=["symbol", "date"], how="left")
        .with_columns(pl.col("volume").fill_null(0), pl.col("value").fill_null(0.0), age=pl.col("day") - pl.col("first"))
        .drop("first")
        .sort("symbol", "day")
    )


def band_side(price: pl.Expr, base: pl.Expr) -> pl.Expr:
    """+1 / -1 when price is within a tick of an upper / lower 2, 5, 10 or 20% band around base, else 0."""

    def near(move: float) -> pl.Expr:
        return (price - base * (1 + move)).abs() <= TICK + 1e-9

    upper = pl.any_horizontal([near(b) for b in BANDS])
    lower = pl.any_horizontal([near(-b) for b in BANDS])
    return pl.when(upper).then(1).when(lower).then(-1).otherwise(0).cast(pl.Int8)


def band_flags(grid: pl.DataFrame, hits: pl.DataFrame) -> pl.DataFrame:
    """Band indicators on raw (unadjusted) prices, NSE's band base being the day's PREVCLOSE, confirmed by
    NSE's band-hit list (PLAN J6 as amended at M2: the price rule alone also flags 2% and 5% moves of
    stocks whose band is wider). `hits`: (symbol, date, side) from the bh files.

    last_band: LAST_t sits on the band NSE lists for day t. open_band_next: O_{t+1} sits on the band
    listed for day t+1. band_hit: the stock is on the day's list."""
    base = pl.col("prev_close")
    listed = hits.select("symbol", "date", nse_side="side").unique(["symbol", "date"])
    g = grid.join(listed, on=["symbol", "date"], how="left").sort("symbol", "day")

    def confirmed(price: pl.Expr) -> pl.Expr:
        side = band_side(price, base)
        return pl.when(side == pl.col("nse_side")).then(side).otherwise(0).cast(pl.Int8)

    return g.with_columns(
        last_band=confirmed(pl.col("last")),
        open_band_next=confirmed(pl.col("open")).shift(-1).over("symbol"),
        band_hit=pl.col("nse_side").is_not_null(),
    ).drop("nse_side")


def legs(grid: pl.DataFrame) -> pl.DataFrame:
    """Log-return legs around each day t (PLAN J8). Short legs need actual trades; decay legs and
    placebos carry the last traded price over no-trade days, flagged by `stale`."""
    g = grid.with_columns(last_ff=pl.col("last").forward_fill().over("symbol"))
    last, open_, carried = pl.col("last"), pl.col("open"), pl.col("last_ff")

    def leg(ratio: pl.Expr) -> pl.Expr:
        return ratio.log().over("symbol")

    return g.with_columns(
        ov=leg(open_ / last.shift(1)),
        id=leg(last / open_),
        g1=leg(open_.shift(-1) / last),
        g1c=leg(open_.shift(-1) / pl.col("close")),
        d15=leg(carried.shift(-5) / open_.shift(-1)),
        d620=leg(carried.shift(-20) / carried.shift(-5)),
        r_m1=leg(carried.shift(1) / carried.shift(2)),
        r_m5=leg(carried.shift(1) / carried.shift(6)),
        stale=(last.shift(-5).is_null() | last.shift(-20).is_null()).over("symbol"),
    ).drop("last_ff")


def windows(grid: pl.DataFrame) -> pl.DataFrame:
    """Predetermined liquidity and volatility; needs `disclosed` and `band_hit` (PLAN section 5).

    adv, med_value: medians of volume and traded value over [t-60, t-6] without disclosure days, at
    least 30 valid days. sigma: Garman-Klass over [t-70, t-11] without disclosure or band-hit days,
    at least 40 valid days."""
    keep = ~pl.col("disclosed")
    gk = 0.5 * (pl.col("high") / pl.col("low")).log() ** 2 - GK_COEF * (pl.col("last") / pl.col("open")).log() ** 2
    return grid.with_columns(
        adv=pl.when(keep).then(pl.col("volume")).rolling_median(55, min_samples=30).shift(6).over("symbol"),
        med_value=pl.when(keep).then(pl.col("value")).rolling_median(55, min_samples=30).shift(6).over("symbol"),
        sigma=pl.when(keep & ~pl.col("band_hit")).then(gk).rolling_mean(60, min_samples=40).sqrt().shift(11).over("symbol"),
    )


def mark_eligible(grid: pl.DataFrame) -> pl.DataFrame:
    """`eligible`: no disclosure in the stock over [t-5, t+20], so it can serve as a benchmark."""
    window = pl.col("disclosed").cast(pl.Int8).rolling_max(26).shift(-20).over("symbol")
    return grid.with_columns(eligible=(window == 0).fill_null(False))


def bucket_cuts(grid: pl.DataFrame) -> pl.DataFrame:
    """Per-date breakpoints from usable eligible rows: med_value quintiles and sigma terciles."""
    return grid.filter(USABLE).group_by("date").agg(
        *(pl.col("med_value").quantile(q).alias(f"_v{i}") for i, q in enumerate(_VALUE_CUTS)),
        *(pl.col("sigma").quantile(q).alias(f"_s{i}") for i, q in enumerate(_SIGMA_CUTS)),
    )


def add_buckets(grid: pl.DataFrame, cuts: pl.DataFrame) -> pl.DataFrame:
    """`bucket` = 3 x med_value quintile + sigma tercile (0-14); null when either input is missing."""
    # Python sum keeps nulls, where sum_horizontal would count them as zero.
    value_rank = sum((pl.col("med_value") > pl.col(f"_v{i}")).cast(pl.Int8) for i in range(len(_VALUE_CUTS)))
    sigma_rank = sum((pl.col("sigma") > pl.col(f"_s{i}")).cast(pl.Int8) for i in range(len(_SIGMA_CUTS)))
    return grid.join(cuts, on="date", how="left").with_columns(bucket=3 * value_rank + sigma_rank).drop(cuts.columns[1:])


def bucket_means(grid: pl.DataFrame) -> pl.DataFrame:
    """Equal-weighted mean of every leg per (date, bucket) over usable eligible rows."""
    return grid.filter(USABLE).group_by("date", "bucket").agg(pl.col(list(LEGS)).mean().name.suffix("_bm"))


def abnormal(grid: pl.DataFrame, means: pl.DataFrame) -> pl.DataFrame:
    """<leg>_abn = leg minus its same-date, same-bucket benchmark mean."""
    return (
        grid.join(means, on=["date", "bucket"], how="left")
        .with_columns((pl.col(leg) - pl.col(f"{leg}_bm")).alias(f"{leg}_abn") for leg in LEGS)
        .drop([f"{leg}_bm" for leg in LEGS])
    )


def benchmark(grid: pl.DataFrame) -> pl.DataFrame:
    """All benchmark steps on one in-memory frame."""
    with_buckets = add_buckets(grid, bucket_cuts(grid))
    return abnormal(with_buckets, bucket_means(with_buckets))
