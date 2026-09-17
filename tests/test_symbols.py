from datetime import date

import polars as pl

from impact.clean import symbols


def test_read_symbol_changes_handles_commas_and_empty_names(tmp_path):
    path = tmp_path / "symbolchange.csv"
    path.write_text(
        " NIPPON INDIA MF - Dual Advantage, Plan E -  GO,RDAXEDG,NDAXEDG,30-OCT-2019\n,780LTFL30,799LTFL30,19-FEB-2025\n",
        encoding="latin-1",
    )
    assert symbols.read_symbol_changes(path).rows() == [
        ("RDAXEDG", "NDAXEDG", date(2019, 10, 30)),
        ("780LTFL30", "799LTFL30", date(2025, 2, 19)),
    ]


def test_chains_and_reused_symbols_map_by_date():
    changes = pl.DataFrame(
        [("A", "B", date(2015, 1, 1)), ("B", "C", date(2018, 6, 1)), ("A", "D", date(2021, 3, 1))],
        schema=["old", "new", "date"],
        orient="row",
    )
    rows = pl.DataFrame(
        [
            ("A", date(2014, 12, 31)),  # first company, before A -> B -> C
            ("B", date(2016, 1, 1)),
            ("C", date(2019, 1, 1)),
            ("A", date(2017, 1, 1)),  # A reused by a second company, renamed D in 2021
            ("A", date(2022, 1, 1)),  # A reused again, no later change
            ("X", date(2014, 1, 1)),
        ],
        schema=["symbol", "date"],
        orient="row",
    )
    out = symbols.to_canonical(rows, symbols.canonical_breaks(changes))
    assert dict(zip(zip(out["symbol"], out["date"]), out["canonical"])) == {
        ("A", date(2014, 12, 31)): "C",
        ("B", date(2016, 1, 1)): "C",
        ("C", date(2019, 1, 1)): "C",
        ("A", date(2017, 1, 1)): "D",
        ("A", date(2022, 1, 1)): "A",
        ("X", date(2014, 1, 1)): "X",
    }
