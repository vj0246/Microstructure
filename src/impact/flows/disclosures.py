"""Disclosed deals to flows: client-name normalization, block-window quantity removal, exact
negotiated crosses, net imbalance per client-day and per stock-day, stock-day types, isolation,
novelty and news filters."""

from datetime import date, time

import polars as pl

_WORD_FIXES = ((r"\bLIMITED\b", "LTD"), (r"\bPRIVATE\b", "PVT"), (r"\bCOMPANY\b", "CO"))


def normalize_client(name: pl.Expr) -> pl.Expr:
    """Uppercase, punctuation to spaces, common legal-form words shortened, whitespace collapsed."""
    out = name.str.to_uppercase().str.replace_all(r"[^A-Z0-9 ]", " ")
    for pattern, short in _WORD_FIXES:
        out = out.str.replace_all(pattern, short)
    return out.str.replace_all(r"\s+", " ").str.strip_chars()


def with_client_norm(deals: pl.DataFrame) -> pl.DataFrame:
    return deals.with_columns(client_norm=normalize_client(pl.col("client")))


def net_out_block(bulk: pl.DataFrame, block: pl.DataFrame) -> pl.DataFrame:
    """Remove block-window quantity from bulk rows. NSE's bulk file aggregates a client's day across
    both trading windows, so block legs are subtracted by stock-day, client and side; the remainder's
    average price is re-derived and rows left with nothing are dropped."""
    key = ["date", "symbol", "client_norm", "side"]
    blocks = block.group_by(key).agg(block_qty=pl.col("qty").sum(), block_value=(pl.col("qty") * pl.col("price")).sum())
    return (
        bulk.join(blocks, on=key, how="left")
        .with_columns(pl.col("block_qty").fill_null(0), pl.col("block_value").fill_null(0.0))
        .with_columns(rest=pl.col("qty") - pl.col("block_qty"))
        .filter(pl.col("rest") > 0)
        .with_columns(
            price=pl.when(pl.col("block_qty") > 0)
            .then((pl.col("qty") * pl.col("price") - pl.col("block_value")) / pl.col("rest"))
            .otherwise(pl.col("price")),
            qty=pl.col("rest"),
        )
        .drop("block_qty", "block_value", "rest")
    )


def flag_crosses(deals: pl.DataFrame) -> pl.DataFrame:
    """Add `cross`: the row is one leg of an exact negotiated cross (PLAN J12).

    Legs share stock-day, quantity and price to the paisa, sit on opposite sides and belong to
    different clients; buyers and sellers pair 1:1 in client-name order. A client on both sides of
    a group is a round trip of its own, not a cross."""
    key = ["date", "symbol", "qty", "paise"]
    df = deals.with_row_index("row").with_columns(paise=(pl.col("price") * 100).round().cast(pl.Int64))
    ranked = (
        df.filter(pl.col("side").n_unique().over([*key, "client_norm"]) == 1)
        .sort([*key, "side", "client_norm"])
        .with_columns(k=pl.int_range(pl.len()).over([*key, "side"]))
    )
    buys = ranked.filter(pl.col("side") == "BUY").select([*key, "k", pl.col("row").alias("buy_row")])
    sells = ranked.filter(pl.col("side") == "SELL").select([*key, "k", pl.col("row").alias("sell_row")])
    pairs = buys.join(sells, on=[*key, "k"])
    legs = pl.concat([pairs["buy_row"], pairs["sell_row"]])
    return df.with_columns(cross=pl.col("row").is_in(legs.implode())).drop("row", "paise")


def client_days(deals: pl.DataFrame) -> pl.DataFrame:
    """One row per (date, symbol, client): buy/sell quantity and value, crossed quantity, net, gross."""
    buy = pl.col("side") == "BUY"
    value = pl.col("qty") * pl.col("price")
    return (
        deals.group_by("date", "symbol", "client_norm")
        .agg(
            client=pl.col("client").first(),
            security=pl.col("security").first(),
            buy_qty=pl.col("qty").filter(buy).sum(),
            sell_qty=pl.col("qty").filter(~buy).sum(),
            buy_value=value.filter(buy).sum(),
            sell_value=value.filter(~buy).sum(),
            cross_qty=pl.col("qty").filter(pl.col("cross")).sum(),
        )
        .with_columns(net=pl.col("buy_qty") - pl.col("sell_qty"), gross=pl.col("buy_qty") + pl.col("sell_qty"))
        .sort("date", "symbol", "client_norm")
    )


def stock_day_types(clients: pl.DataFrame, share: float = 0.8) -> pl.DataFrame:
    """Per stock-day (PLAN J13): `side` = sign of net flow; `stock_type` = the trader type supplying at
    least `share` of the event side's quantity from directional, non-churner, non-cross client-days,
    else "mixed", or "none" when no such quantity exists; `type_share` = that top share.
    `clients`: typed client-days (ctype, directional, buy_qty, sell_qty, cross_qty, net)."""
    day = clients.group_by("date", "symbol").agg(side=pl.col("net").sum().sign())
    event_qty = pl.when(pl.col("side") > 0).then(pl.col("buy_qty")).otherwise(pl.col("sell_qty")) - pl.col("cross_qty")
    ranked = (
        clients.join(day, on=["date", "symbol"])
        .filter(pl.col("directional") & (pl.col("ctype") != "P") & (pl.col("side") != 0))
        .with_columns(q=event_qty.clip(lower_bound=0))
        .group_by("date", "symbol", "ctype")
        .agg(pl.col("q").sum())
        .sort("q", descending=True)
        .group_by("date", "symbol", maintain_order=True)
        .agg(top=pl.col("ctype").first(), top_q=pl.col("q").first(), total=pl.col("q").sum())
    )
    return (
        day.join(ranked, on=["date", "symbol"], how="left")
        .with_columns(type_share=pl.col("top_q") / pl.col("total"))
        .with_columns(
            stock_type=pl.when(pl.col("total").fill_null(0) <= 0)
            .then(pl.lit("none"))
            .when(pl.col("type_share") >= share)
            .then(pl.col("top"))
            .otherwise(pl.lit("mixed"))
        )
        .select("date", "symbol", "side", "stock_type", "type_share")
        .sort("date", "symbol")
    )


def event_flags(clients: pl.DataFrame, days: pl.DataFrame, calendar: pl.DataFrame) -> pl.DataFrame:
    """Per stock-day, over the client-days driving it (event side, directional, not churners):
    `isolated` (PLAN J2) = none of them disclosed in this stock over the previous 20 trading days, and
    nobody disclosed flow in this stock over the previous 5; `novel` (J1) = none of them disclosed in
    any stock over the previous 250 trading days. Backward-looking only. `days` carries `side`."""
    c = clients.join(calendar, on="date").sort("client_norm", "symbol", "day")
    c = c.with_columns(gap_stock=pl.col("day") - pl.col("day").shift(1).over("client_norm", "symbol"))
    seen = (
        c.select("client_norm", "day")
        .unique()
        .sort("client_norm", "day")
        .with_columns(gap_any=pl.col("day") - pl.col("day").shift(1).over("client_norm"))
    )
    driving = (
        c.join(seen, on=["client_norm", "day"])
        .join(days.select("date", "symbol", "side"), on=["date", "symbol"])
        .filter(pl.col("directional") & (pl.col("ctype") != "P") & (pl.col("net").sign() == pl.col("side")))
    )
    per_day = driving.group_by("date", "symbol").agg(
        clients_isolated=(pl.col("gap_stock").is_null() | (pl.col("gap_stock") > 20)).all(),
        novel=(pl.col("gap_any").is_null() | (pl.col("gap_any") > 250)).all(),
    )
    stock = (
        days.join(calendar, on="date")
        .sort("symbol", "day")
        .with_columns(gap=pl.col("day") - pl.col("day").shift(1).over("symbol"))
        .select("date", "symbol", stock_isolated=pl.col("gap").is_null() | (pl.col("gap") > 5))
    )
    return (
        stock.join(per_day, on=["date", "symbol"], how="left")
        .with_columns(
            isolated=pl.col("stock_isolated") & pl.col("clients_isolated").fill_null(False),
            novel=pl.col("novel").fill_null(False),
        )
        .select("date", "symbol", "isolated", "novel")
    )


def news_flags(
    days: pl.DataFrame, calendar: pl.DataFrame, board: pl.DataFrame, results: pl.DataFrame, actions: pl.DataFrame
) -> pl.DataFrame:
    """Per stock-day (PLAN J10): `news` = a board meeting or a corporate-action ex-date on t or t+1, or
    a results broadcast between 15:30 on t-1 and 09:00 on t+1; `calendar_skip` = weekend session or
    the last trading day of March or September (Nifty rebalancing close). Symbols must be canonical."""
    cal = calendar.sort("date").with_columns(prev_date=pl.col("date").shift(1), next_date=pl.col("date").shift(-1))
    d = days.select("date", "symbol").join(cal.select("date", "prev_date", "next_date"), on="date", how="left")

    def on_day_or_next(dated: pl.DataFrame) -> pl.DataFrame:
        marks = dated.select("symbol", "date").unique()
        same = d.join(marks, on=["symbol", "date"], how="semi")
        following = d.join(marks.rename({"date": "next_date"}), on=["symbol", "next_date"], how="semi")
        return pl.concat([same, following]).select("date", "symbol")

    windows = d.with_columns(
        start=pl.col("prev_date").dt.combine(time(15, 30)), end=pl.col("next_date").dt.combine(time(9, 0))
    ).sort("start")
    broadcast = windows.join_asof(
        results.sort("broadcast"), left_on="start", right_on="broadcast", by="symbol", strategy="forward", check_sortedness=False
    ).filter(pl.col("broadcast") < pl.col("end"))
    hits = pl.concat([on_day_or_next(board), on_day_or_next(actions), broadcast.select("date", "symbol")]).unique()
    rebalance = (
        cal.filter(pl.col("date").dt.month().is_in([3, 9]))
        .group_by(pl.col("date").dt.year().alias("year"), pl.col("date").dt.month().alias("month"))
        .agg(pl.col("date").max())["date"]
    )
    return d.join(hits.with_columns(news=pl.lit(True)), on=["date", "symbol"], how="left").select(
        "date",
        "symbol",
        news=pl.col("news").fill_null(False),
        calendar_skip=(pl.col("date").dt.weekday() >= 6) | pl.col("date").is_in(rebalance.implode()),
    )


def cross_types(clients: pl.DataFrame) -> pl.DataFrame:
    """Per stock-day with exact crosses (PLAN S3): `buyer_type` and `seller_type`, the types of the
    clients with the largest crossed quantity on each side."""
    legs = clients.filter(pl.col("cross_qty") > 0).with_columns(
        leg=pl.when(pl.col("net") > 0).then(pl.lit("buyer_type")).otherwise(pl.lit("seller_type"))
    )
    top = (
        legs.sort("cross_qty", descending=True)
        .group_by("date", "symbol", "leg", maintain_order=True)
        .agg(pl.col("ctype").first())
    )
    return top.pivot(on="leg", index=["date", "symbol"], values="ctype").drop_nulls().sort("date", "symbol")


FORECAST_MIN = 50  # run-starts a type x tercile cell needs before its own mean is used
FORECAST_DAYS = 5


def continuation_forecast(
    clients: pl.DataFrame, table: pl.DataFrame, calendar: pl.DataFrame, cut_before: date, min_cell: int = FORECAST_MIN
) -> pl.DataFrame:
    """PLAN S1 F-hat per event stock-day: expected same-direction disclosed flow by the same client in
    the same stock over the next 5 trading days, in ADV units.

    Learned point-in-time from run-starts (directional non-churner client-days with no same client-stock
    disclosure in the prior 20 trading days) whose 5-day outcome was complete before the event day: the
    expanding mean of their type x log-x tercile cell once it holds `min_cell` run-starts, else the
    expanding type mean. Tercile cuts come from run-starts dated before `cut_before`. `table` supplies
    adv, cum and x for stock-days (event_table)."""
    c = (
        clients.join(calendar, on="date")
        .sort("client_norm", "symbol", "day")
        .with_columns(sign=pl.col("net").sign(), gap=pl.col("day") - pl.col("day").shift(1).over("client_norm", "symbol"))
    )
    starts = c.filter(pl.col("directional") & (pl.col("ctype") != "P") & (pl.col("gap").is_null() | (pl.col("gap") > 20)))
    later = c.select("client_norm", "symbol", later_day="day", later_net="net")
    flow = (
        starts.select("client_norm", "symbol", "day", "sign")
        .join(later, on=["client_norm", "symbol"])
        .filter(
            (pl.col("later_day") > pl.col("day"))
            & (pl.col("later_day") <= pl.col("day") + FORECAST_DAYS)
            & (pl.col("later_net").sign() == pl.col("sign"))
        )
        .group_by("client_norm", "symbol", "day")
        .agg(cont=pl.col("later_net").abs().sum())
    )
    scale = table.select("symbol", "date", "adv", "cum", "x")
    starts = (
        starts.join(flow, on=["client_norm", "symbol", "day"], how="left")
        .join(scale, on=["symbol", "date"])
        .with_columns(f=pl.col("cont").fill_null(0) * pl.col("cum") / pl.col("adv"), lx=pl.col("x").log10())
    )
    early = starts.filter(pl.col("date") < cut_before)["lx"]
    low, high = early.quantile(1 / 3), early.quantile(2 / 3)

    def tercile(col: str) -> pl.Expr:
        return pl.when(pl.col(col) <= low).then(0).when(pl.col(col) <= high).then(1).otherwise(2)

    starts = starts.with_columns(cell=tercile("lx"), available=pl.col("day") + FORECAST_DAYS).sort("available")

    def expanding(keys: list[str]) -> pl.DataFrame:
        # f >= 0, so the running sum is non-decreasing and its max per day is the running total
        return (
            starts.with_columns(n=pl.int_range(1, pl.len() + 1).over(keys), s=pl.col("f").cum_sum().over(keys))
            .group_by(*keys, "available")
            .agg(pl.col("n").max(), pl.col("s").max())
            .sort("available")
        )

    events = (
        table.filter(pl.col("stock_type").is_in(["I", "R", "S"]))
        .join(calendar, on="date")
        .with_columns(ctype=pl.col("stock_type"), lx=pl.col("x").log10(), query=pl.col("day") - 1)
        .with_columns(cell=tercile("lx"))
        .sort("query")
    )

    def lookup(keys: list[str]) -> pl.DataFrame:
        return events.join_asof(
            expanding(keys), left_on="query", right_on="available", by=keys, check_sortedness=False
        )

    by_cell, by_type = lookup(["ctype", "cell"]), lookup(["ctype"])
    return by_cell.with_columns(type_n=by_type["n"], type_s=by_type["s"]).select(
        "date",
        "symbol",
        f_hat=pl.when(pl.col("n") >= min_cell).then(pl.col("s") / pl.col("n")).otherwise(pl.col("type_s") / pl.col("type_n")),
    )


def stock_days(clients: pl.DataFrame) -> pl.DataFrame:
    """Net disclosed imbalance per stock-day; crosses cancel inside the sum."""
    return (
        clients.group_by("date", "symbol")
        .agg(
            net=pl.col("net").sum(),
            gross=pl.col("gross").sum(),
            clients=pl.len(),
            crossed=pl.col("cross_qty").sum() // 2,
            buy_qty=pl.col("buy_qty").sum(),  # quantities and values give the event-side average price
            sell_qty=pl.col("sell_qty").sum(),
            buy_value=pl.col("buy_value").sum(),
            sell_value=pl.col("sell_value").sum(),
        )
        .sort("date", "symbol")
    )
