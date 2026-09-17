from datetime import date

import polars as pl

from impact import gates

HEADER = '"Date ","Symbol ","Security Name ","Client Name ","Buy / Sell ","Quantity Traded ","Trade Price / Wght. Avg. Price ","Remarks "\n'
ROW = '"02-JAN-2012","ABC","Abc Ltd","X","BUY","1,000","10.00","-"\n'


def write(folder, name, rows):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(HEADER + ROW * rows, encoding="utf-8")


def test_half_year_windows_must_sum_to_full_year(tmp_path):
    folder = tmp_path / "deals" / "bulk_deals"
    write(folder, "bulk_deals_2012-01-01_2012-12-31.csv", 3)
    write(folder, "bulk_deals_2012-01-01_2012-06-30.csv", 1)
    write(folder, "bulk_deals_2012-07-01_2012-12-31.csv", 1)
    write(folder, "bulk_deals_2013-01-01_2013-12-31.csv", 2)
    write(folder, "bulk_deals_2013-01-01_2013-06-30.csv", 1)
    write(folder, "bulk_deals_2013-07-01_2013-12-31.csv", 1)
    lines = gates._deal_halves(tmp_path)
    assert lines == ["FAIL bulk_deals 2012: 3 rows, halves 2", "ok   bulk_deals 2013: 2 rows, halves 2"]
    assert gates.year_rows(folder) == {2012: 3, 2013: 2}


def test_churner_dominated_days_compare_delivery_within_bucket_and_year(tmp_path):
    d = date(2015, 3, 2)
    # CH is 90% churner gross with 10% delivery; its bucket peers deliver 40-60%; bucket 2 has no peers
    pl.DataFrame(
        {
            "date": [d] * 6,
            "symbol": ["CH", "CH", "A", "B", "C", "LONE"],
            "ctype": ["P", "R", "R", "I", "R", "P"],
            "gross": [90, 10, 100, 100, 100, 100],
        }
    ).write_parquet(tmp_path / "client_days.parquet")
    pl.DataFrame(
        {
            "date": [d] * 5,
            "symbol": ["CH", "A", "B", "C", "LONE"],
            "bucket": [1, 1, 1, 1, 2],
            "deliv_qty": [10, 40, 50, 60, 5],
            "volume": [100] * 5,
        }
    ).write_parquet(tmp_path / "event_table.parquet")
    assert gates._churner_delivery(tmp_path) == [
        "ok   delivery on 1 churner-dominated stock-days: median gap to bucket-year median -40.0%, 100% below it"
    ]
