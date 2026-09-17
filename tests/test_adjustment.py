import math
from datetime import date

import polars as pl
import pytest

from impact.clean import adjustment


@pytest.mark.parametrize(
    ("subject", "factor"),
    [
        ("Bonus 1:1", 2.0),
        ("Bonus 3 : 2", 2.5),
        ("Agm/Div-10%/Bonus 1:2", 1.5),
        ("Bonus Shares In The Ratio Of 1:1", 2.0),
        ("Fv Split Rs.10 To Re.1", 10.0),
        ("Face Value Split (Sub-Division) - From Rs 10/- Per Share To Re 1/- Per Share", 10.0),
        ("Annual General Meeting / Dividend - Rs 5/- Per Share / Face Value Split - From Rs 10/- Per Share To Rs 2/- Per Share", 5.0),
        ("1St Interim Dividend Rs.5 Per Share And Face Value Split From Rs.10/- To Re.1/- (Purpose Revised)", 10.0),
        ("Bonus 1:1 / Face Value Split From 10/- To Face Value 5/-", 4.0),
        ("Bonus - 1:1 And Face Value Split From Rs. 10 To Rs. 5", 4.0),
        ("Consolidation Of Shares From Rs 1/- To Rs 10/-", 0.1),
        ("Split-Rs.10tors.2/Div-60%Purpose Revised", 5.0),
        ("Fv Spl-Rs10tore1/Bon-1:1", 20.0),
        ("Div-Rs.5.50 Pr Sh/Bon 5:1purpose Revised", 6.0),
        ("Fv Spl-Rs10to2/Div-Rs2.50", 5.0),
        ("Bon 1:1/Fv Spl Rs.5tors.2", 5.0),
        ("Div- Int Re.1+Spl Rs.1.25", None),
        ("Spl Int Div-Rs.100 P Shr Purpose Revised", None),
        ("Scheme Of Arrangement - Bonus Ncrps 1:10", None),
        ("Interim Dividend Rs.25/- Per Share", None),
        ("Agm/Rights 1:5 Prem@Rs.10 Purpose Revised", None),
        ("Bonus Preference Shares 1:1", None),
        ("Scheme Of Arrangement - Bonus Debentures 1:1", None),
    ],
)
def test_subject_factor(subject, factor):
    if factor is None:
        assert adjustment.subject_factor(subject) is None
    else:
        assert adjustment.subject_factor(subject) == pytest.approx(factor)


def test_action_factors_dedupe_revisions_and_multiply_distinct_events():
    d = date(2024, 1, 3)
    actions = pl.DataFrame(
        {
            "symbol": ["A", "A", "B", "B", "C"],
            "date": [d] * 5,
            "subject": ["Bonus 1:1", "Bonus 1:1 (Record Date Revised)", "Bonus 1:1", "Fv Split Rs.10 To Rs.5", "Final Dividend"],
        }
    )
    assert adjustment.action_factors(actions).rows() == [("A", d, 2.0), ("B", d, 4.0)]


def rows_for(closes, opens):
    return pl.DataFrame(
        {
            "symbol": ["ABC"] * len(closes),
            "date": [date(2024, 1, d) for d in (1, 2, 3, 4)][: len(closes)],
            "open": opens,
            "close": closes,
            "prev_close": [99.0, 100.0, 102.0, 10.3][: len(closes)],  # NSE leaves PREVCLOSE unadjusted
            "volume": [1000, 1000, 10_000, 9000][: len(closes)],
        }
    )


def test_confirmed_split_gives_continuous_prices_and_share_units():
    rows = rows_for([100.0, 102.0, 10.3, 10.4], [99.5, 101.0, 10.25, 10.3])
    factors = pl.DataFrame({"symbol": ["ABC"], "date": [date(2024, 1, 3)], "factor": [10.0]})
    audit = adjustment.confirm(rows, factors)
    assert audit["confirmed"].to_list() == [True]
    applied = audit.select("symbol", pl.col("trade_date").alias("date"), "factor")
    out = adjustment.apply_factors(rows, applied)
    assert out["cum"].to_list() == pytest.approx([10.0, 10.0, 1.0, 1.0])
    assert out["close"].to_list() == pytest.approx([10.0, 10.2, 10.3, 10.4])
    assert out["volume"].to_list() == pytest.approx([10_000, 10_000, 10_000, 9000])
    assert math.log(out["close"][2] / out["close"][1]) == pytest.approx(math.log(10.3 / 10.2))


def test_factor_moves_to_next_trading_day_and_needs_a_jump():
    rows = rows_for([100.0, 102.0, 103.0, 104.0], [99.5, 101.0, 102.5, 103.5]).filter(pl.col("date") != date(2024, 1, 2))
    factors = pl.DataFrame({"symbol": ["ABC"], "date": [date(2024, 1, 2)], "factor": [2.0]})
    audit = adjustment.confirm(rows, factors).row(0, named=True)
    assert audit["trade_date"] == date(2024, 1, 3)  # ex-date without trades moves forward
    assert (audit["confirmed"], audit["apply"]) == (False, False)  # 100 -> 102.5 overnight is no 2:1 jump


def test_arrangement_takes_a_clear_jump_as_its_factor():
    rows = rows_for([100.0, 102.0, 60.0, 61.0], [99.5, 101.0, 61.0, 60.5])
    actions = pl.DataFrame(
        {
            "symbol": ["ABC", "ABC"],
            "date": [date(2024, 1, 3), date(2024, 1, 4)],
            "subject": ["Scheme Of Arrangement", "Capital Reduction"],
        }
    )
    audit = adjustment.arrangement_factors(rows, actions, pl.DataFrame(schema={"symbol": pl.String, "date": pl.Date}))
    demerger, reduction = audit.sort("date").rows(named=True)
    assert (demerger["apply"], demerger["factor"]) == (True, pytest.approx(102.0 / 61.0))
    assert reduction["apply"] is False  # 60 -> 60.5 is no gap
    known = pl.DataFrame({"symbol": ["ABC"], "date": [date(2024, 1, 3)]})
    assert adjustment.arrangement_factors(rows, actions, known)["date"].to_list() == [date(2024, 1, 4)]


def test_small_factor_is_applied_as_dated_without_confirmation():
    rows = rows_for([100.0, 102.0, 100.0, 101.0], [99.5, 101.0, 100.5, 100.0])
    factors = pl.DataFrame({"symbol": ["ABC"], "date": [date(2024, 1, 3)], "factor": [1.1]})
    audit = adjustment.confirm(rows, factors).row(0, named=True)
    assert (audit["confirmed"], audit["apply"]) == (False, True)
