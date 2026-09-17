from datetime import date, datetime, time, timedelta

import polars as pl
import pytest

from impact.flows import disclosures

D = date(2024, 1, 5)


def deals(rows):
    frame = pl.DataFrame(rows, schema=["date", "symbol", "client", "side", "qty", "price"], orient="row")
    return disclosures.with_client_norm(frame.with_columns(security=pl.col("symbol") + " Ltd"))


def test_normalize_client():
    names = pl.DataFrame({"c": ["Graviton Research Capital LLP.", "abc  Private   Limited", "M/s. X & Co"]})
    assert names.select(disclosures.normalize_client(pl.col("c")))["c"].to_list() == [
        "GRAVITON RESEARCH CAPITAL LLP",
        "ABC PVT LTD",
        "M S X CO",
    ]


def test_exact_crosses_pair_distinct_clients_one_to_one():
    df = disclosures.flag_crosses(
        deals(
            [
                (D, "ABC", "Promoter A", "SELL", 1000, 10.00),
                (D, "ABC", "Fund B", "BUY", 1000, 10.00),
                (D, "ABC", "Churner C", "BUY", 500, 10.00),
                (D, "ABC", "Churner C", "SELL", 500, 10.00),
                (D, "ABC", "Buyer D", "BUY", 1000, 10.01),
                (D, "XYZ", "Buyer E", "BUY", 1000, 10.00),
                (D, "PQR", "B1", "BUY", 200, 5.00),
                (D, "PQR", "B2", "BUY", 200, 5.00),
                (D, "PQR", "S1", "SELL", 200, 5.00),
            ]
        )
    )
    assert df["cross"].to_list() == [True, True, False, False, False, False, True, False, True]


def test_stock_day_net_cancels_crosses_and_round_trips():
    flagged = disclosures.flag_crosses(
        deals(
            [
                (D, "ABC", "Promoter A", "SELL", 1000, 10.00),
                (D, "ABC", "Fund B", "BUY", 1000, 10.00),
                (D, "ABC", "Churner C", "BUY", 500, 10.00),
                (D, "ABC", "Churner C", "SELL", 500, 10.02),
                (D, "ABC", "Buyer D", "BUY", 1000, 10.01),
            ]
        )
    )
    clients = disclosures.client_days(flagged)
    churner = clients.filter(pl.col("client_norm") == "CHURNER C").row(0, named=True)
    assert (churner["net"], churner["gross"], churner["cross_qty"]) == (0, 1000, 0)
    day = disclosures.stock_days(clients).row(0, named=True)
    assert (day["net"], day["gross"], day["clients"], day["crossed"]) == (1000, 4000, 4, 1000)


def test_stock_day_type_needs_eighty_percent_of_clean_event_side_flow():
    rows = [
        # date, symbol, client_norm, buy, sell, cross, directional, ctype
        (D, "ABC", "SBI MF", 900, 0, 0, True, "I"),
        (D, "ABC", "A PERSON", 100, 0, 0, True, "R"),
        (D, "ABC", "FAST DESK", 500, 450, 0, False, "P"),  # churn counts in net, never in the type
        (D, "XYZ", "SBI MF", 500, 0, 0, True, "I"),
        (D, "XYZ", "A PERSON", 500, 0, 0, True, "R"),
        (D, "QQQ", "FAST DESK", 800, 0, 0, True, "P"),
        (D, "CRS", "FUND B", 1000, 0, 1000, True, "I"),  # crossed quantity is not event-side flow
        (D, "CRS", "A PERSON", 200, 0, 0, True, "R"),
    ]
    clients = pl.DataFrame(
        rows, schema=["date", "symbol", "client_norm", "buy_qty", "sell_qty", "cross_qty", "directional", "ctype"], orient="row"
    ).with_columns(net=pl.col("buy_qty") - pl.col("sell_qty"))
    out = {s: (side, t) for s, side, t in disclosures.stock_day_types(clients).select("symbol", "side", "stock_type").rows()}
    assert out == {"ABC": (1, "I"), "XYZ": (1, "mixed"), "QQQ": (1, "none"), "CRS": (1, "R")}


def calendar(n):
    return pl.DataFrame({"date": [date(2024, 1, 1) + timedelta(days=i) for i in range(n)]}).with_row_index("day").with_columns(
        pl.col("day").cast(pl.Int64)
    )


def test_isolation_and_novelty_look_backward_only():
    cal = calendar(60)
    day = cal["date"].to_list()
    rows = [
        # date, symbol, client_norm, net, ctype
        (day[10], "X", "A", 100, "I"),  # first sighting: isolated and novel
        (day[25], "X", "A", 100, "I"),  # same client, same stock 15 days on
        (day[37], "Z", "C", 100, "R"),
        (day[40], "Y", "C", 100, "R"),  # new stock for C, but C disclosed 3 days ago
        (day[47], "W", "E", 100, "R"),
        (day[50], "W", "D", 100, "R"),  # someone disclosed W 3 days ago
    ]
    clients = pl.DataFrame(rows, schema=["date", "symbol", "client_norm", "net", "ctype"], orient="row").with_columns(
        directional=pl.lit(True)
    )
    days = clients.group_by("date", "symbol").agg(side=pl.col("net").sum().sign())
    out = {(d, s): (iso, nov) for d, s, iso, nov in disclosures.event_flags(clients, days, cal).rows()}
    assert out[(day[10], "X")] == (True, True)
    assert out[(day[25], "X")] == (False, False)
    assert out[(day[40], "Y")] == (True, False)
    assert out[(day[50], "W")] == (False, True)


def test_news_flags_windows():
    cal = calendar(200).filter(pl.col("date").dt.weekday() < 6)
    t, t_prev, t_next = date(2024, 1, 10), date(2024, 1, 9), date(2024, 1, 11)
    days = pl.DataFrame({"date": [t, t, t, t, t, date(2024, 3, 29)], "symbol": ["BM", "RS", "EARLY", "EX", "CLEAN", "MAR"]})
    board = pl.DataFrame({"symbol": ["BM"], "date": [t_next]})
    results = pl.DataFrame(
        {
            "symbol": ["RS", "EARLY"],
            "broadcast": [datetime.combine(t_prev, time(16, 0)), datetime.combine(t_prev, time(14, 0))],
        }
    )
    actions = pl.DataFrame({"symbol": ["EX"], "date": [t]})
    out = {s: (n, c) for _, s, n, c in disclosures.news_flags(days, cal, board, results, actions).rows()}
    assert out == {
        "BM": (True, False),
        "RS": (True, False),  # after the t-1 close
        "EARLY": (False, False),  # during t-1 trading, already in the t-1 close
        "EX": (True, False),
        "CLEAN": (False, False),
        "MAR": (False, True),  # 2024-03-29 is the last weekday of March in this calendar
    }


def test_continuation_forecast_is_point_in_time():
    cal = calendar(80)
    day = cal["date"].to_list()

    def forecast(extra, zero_adv=()):
        rows = [(day[10], "X", 100), (day[12], "X", 200), (day[30], "Y", 100), *extra]  # client A, type I
        clients = pl.DataFrame(rows, schema=["date", "symbol", "net"], orient="row").with_columns(
            client_norm=pl.lit("A"), directional=pl.lit(True), ctype=pl.lit("I")
        )
        stock_days = [(day[10], "X"), (day[12], "X"), (day[30], "Y"), (day[32], "Y"), (day[15], "Z"), (day[16], "Z"), (day[70], "Z")]
        table = pl.DataFrame(stock_days + list(zero_adv), schema=["date", "symbol"], orient="row").with_columns(
            adv=pl.when(pl.struct("date", "symbol").is_in([{"date": d, "symbol": s} for d, s in zero_adv]))
            .then(0.0).otherwise(100.0),
            cum=pl.lit(1.0), x=pl.lit(1.0), stock_type=pl.lit("I"),
        )
        out = disclosures.continuation_forecast(clients, table, cal, cut_before=day[50], min_cell=1)
        return {d: f for d, s, f in out.filter(pl.col("symbol") == "Z").rows()}

    base = forecast([])
    assert base[day[15]] is None  # the day-10 run-start's outcome completes on day 15, not before
    assert base[day[16]] == pytest.approx(2.0)  # 200 shares on day 12 / ADV 100
    assert base[day[70]] == pytest.approx(1.0)  # mean of 2.0 and the day-30 start's 0.0
    later = forecast([(day[32], "Y", 300)])  # continuation that happens after day 16
    illiquid = forecast([(day[40], "W", 100)], zero_adv=[(day[40], "W")])  # a run-start with ADV 0 is ignored
    assert illiquid[day[70]] == pytest.approx(1.0)
    assert later[day[16]] == base[day[16]] and later[day[70]] == pytest.approx(2.5)


def test_cross_types_take_the_largest_leg_on_each_side():
    rows = [
        # symbol, client, net, cross_qty, ctype
        ("ABC", "FUND", 1000, 1000, "I"),
        ("ABC", "PROMOTER", -1000, 1000, "S"),
        ("ABC", "SMALL", 50, 50, "R"),
        ("XYZ", "PERSON", 400, 400, "R"),
        ("XYZ", "BANK", -400, 400, "I"),
        ("QQQ", "LONE", 500, 0, "R"),
    ]
    clients = pl.DataFrame(rows, schema=["symbol", "client_norm", "net", "cross_qty", "ctype"], orient="row").with_columns(
        date=pl.lit(D)
    )
    out = {s: (b, sl) for _, s, b, sl in disclosures.cross_types(clients).select("date", "symbol", "buyer_type", "seller_type").rows()}
    assert out == {"ABC": ("I", "S"), "XYZ": ("R", "I")}


def test_net_out_block_removes_block_legs_and_reprices_the_rest():
    bulk = deals(
        [
            (D, "ABC", "Fund B", "BUY", 1000, 10.0),  # all of it was the block leg
            (D, "XYZ", "Fund B", "BUY", 1500, 10.2),  # 1000 block at 10.0 plus 500 in the normal market
            (D, "XYZ", "Seller S", "SELL", 800, 10.1),  # no block leg
        ]
    )
    block = deals([(D, "ABC", "FUND B", "BUY", 1000, 10.0), (D, "XYZ", "FUND B.", "BUY", 1000, 10.0)])
    out = disclosures.net_out_block(bulk, block).sort("symbol", "client_norm")
    assert out.select("symbol", "client_norm", "qty").rows() == [("XYZ", "FUND B", 500), ("XYZ", "SELLER S", 800)]
    assert out["price"].to_list() == pytest.approx([10.6, 10.1])
