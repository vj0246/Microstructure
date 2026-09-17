import math
from datetime import date, timedelta

import polars as pl
import pytest

from impact.clean import panel

D0 = date(2024, 1, 1)


def days(n):
    return [D0 + timedelta(days=i) for i in range(n)]


def series(symbol, n, **cols):
    return pl.DataFrame({"symbol": [symbol] * n, "date": days(n), **cols})


def test_equity_rows_prefer_eq_and_drop_sme():
    prices = pl.DataFrame(
        {"symbol": ["A", "A", "B"], "date": [D0] * 3, "series": ["BE", "EQ", "SM"], "close": [1.0, 2.0, 3.0]}
    )
    assert panel.equity_rows(prices).rows() == [("A", D0, "EQ", 2.0)]


def test_calendar_fills_no_trade_days_and_carries_past_last_row():
    full = series("B", 5, volume=[1] * 5, value=[1.0] * 5, last=[10.0] * 5)
    thin = pl.DataFrame({"symbol": ["A", "A"], "date": [D0, D0 + timedelta(days=2)], "volume": [7, 9], "value": [7.0, 9.0], "last": [5.0, 6.0]})
    prices = pl.concat([full, thin])
    grid = panel.on_calendar(prices, panel.market_calendar(prices["date"]))
    a = grid.filter(pl.col("symbol") == "A")
    assert a["volume"].to_list() == [7, 0, 9, 0, 0]  # carried to the calendar end, capped
    assert a["last"].to_list() == [5.0, None, 6.0, None, None]
    assert a["age"].to_list() == [0, 1, 2, 3, 4]


def test_legs_follow_definitions_and_carry_stale_prices():
    n = 40
    opens = [100.0 + t for t in range(n)]
    lasts = [100.5 + t for t in range(n)]
    lasts[10] = None  # no trade on day 10
    closes = [100.25 + t for t in range(n)]
    grid = series("A", n, open=opens, last=lasts, close=closes).with_columns(day=pl.int_range(n))
    out = panel.legs(grid)
    row = out.row(5, named=True)
    assert row["g1"] == pytest.approx(math.log(106.0 / 105.5))
    assert row["g1c"] == pytest.approx(math.log(106.0 / 105.25))
    assert row["ov"] + row["id"] == pytest.approx(math.log(105.5 / 104.5))
    assert row["d15"] == pytest.approx(math.log(109.5 / 106.0))  # day 10 carries day 9's last
    assert row["stale"] is True
    assert out.row(6, named=True)["stale"] is False


def test_windows_exclude_disclosure_days_and_lag_the_window():
    n = 100
    grid = series(
        "A",
        n,
        volume=list(range(n)),
        value=[float(v) for v in range(n)],
        open=[100.0] * n,
        last=[100.0] * n,
        high=[100.0 * math.exp(0.02)] * n,
        low=[100.0] * n,
        disclosed=[t == 50 for t in range(n)],
        band_hit=[False] * n,
    )
    out = panel.windows(grid)
    assert out["adv"][80] == pytest.approx(float(pl.Series([v for v in range(20, 75) if v != 50]).median()))
    assert out["adv"][34] is None and out["adv"][35] == pytest.approx(14.5)
    assert out["sigma"][49] is None
    assert out["sigma"][50] == pytest.approx(math.sqrt(0.5 * 0.02**2))


def test_band_side():
    frame = pl.DataFrame({"price": [105.0, 90.03, 107.0, 102.04, None], "base": [100.0] * 5})
    assert frame.select(panel.band_side(pl.col("price"), pl.col("base"))).to_series().to_list() == [1, -1, 0, 1, 0]


def test_band_flags_need_nse_confirmation():
    grid = pl.DataFrame(
        {
            "symbol": ["A"] * 3,
            "date": days(3),
            "day": [0, 1, 2],
            "prev_close": [100.0, 100.0, 105.0],
            "last": [105.0, 105.0, 110.0],
            "open": [100.0, 101.0, 110.25],
            "high": [105.0, 105.0, 110.25],
            "low": [99.0, 100.0, 105.0],
        }
    )
    hits = pl.DataFrame({"symbol": ["A", "A"], "date": days(3)[1:], "side": [1, 1]}).with_columns(pl.col("side").cast(pl.Int8))
    out = panel.band_flags(grid, hits)
    assert out["last_band"].to_list() == [0, 1, 0]  # day 0 moves 5% but NSE does not list it; day 2 is listed but LAST is off-band
    assert out["open_band_next"].to_list() == [0, 1, None]  # day 2 opens on its listed +5% band
    assert out["band_hit"].to_list() == [False, True, True]


def test_eligible_needs_clean_window_around_the_day():
    grid = series("A", 60, disclosed=[t == 25 for t in range(60)])
    eligible = panel.mark_eligible(grid)["eligible"].to_list()
    assert [t for t, ok in enumerate(eligible) if ok] == list(range(31, 40))


def test_benchmark_subtracts_same_bucket_mean_of_eligible_stocks():
    stocks = ["E1", "E2", "E3", "E4", "E5", "X"]
    frame = pl.DataFrame(
        {
            "symbol": stocks,
            "date": [D0] * 6,
            "eligible": [True] * 5 + [False],
            "med_value": [1.0, 2.0, 3.0, 4.0, 5.0, 5.0],
            "sigma": [0.02] * 6,
            **{leg: [0.0] * 6 for leg in panel.LEGS},
        }
    ).with_columns(g1=pl.Series([0.01, 0.02, 0.03, 0.04, 0.05, 0.09]))
    out = panel.benchmark(frame)
    event = out.filter(pl.col("symbol") == "X").row(0, named=True)
    assert event["bucket"] == out.filter(pl.col("symbol") == "E5")["bucket"][0]
    peers = out.filter(pl.col("eligible") & (pl.col("bucket") == event["bucket"]))
    assert event["g1_abn"] == pytest.approx(0.09 - peers["g1"].mean())
