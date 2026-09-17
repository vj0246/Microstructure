"""Security master: map every (symbol, date) to one canonical symbol by following NSE symbol changes.

Keyed on symbol chains with date ranges, not ISIN (ISIN changes on face-value splits and is missing
from early files). A retired symbol that NSE later reuses maps to its new owner after the change."""

from datetime import date, datetime
from functools import cache
from pathlib import Path

import polars as pl


def read_symbol_changes(path: Path) -> pl.DataFrame:
    """symbolchange.csv, no header: company name (may contain commas or be empty), old, new, DD-MON-YYYY."""
    rows = []
    for line in path.read_text(encoding="latin-1").splitlines():
        if line.strip():
            _, old, new, when = line.rsplit(",", 3)
            if old.strip() != new.strip():  # NSE lists some no-op X -> X records
                rows.append((old.strip(), new.strip(), datetime.strptime(when.strip().title(), "%d-%b-%Y").date()))
    return pl.DataFrame(rows, schema=["old", "new", "date"], orient="row").unique().sort("date", "old")


def canonical_breaks(changes: pl.DataFrame) -> pl.DataFrame:
    """Rows (symbol, until, canonical): a row of `symbol` dated before `until` belongs to `canonical`."""
    by_old: dict[str, list[tuple[date, str]]] = {}
    for old, new, when in changes.select("old", "new", "date").iter_rows():
        by_old.setdefault(old, []).append((when, new))
    for moves in by_old.values():
        moves.sort()

    @cache
    def final(symbol: str, since: date) -> str:
        # Strictly later changes only, so dates increase along the chain and it always terminates.
        move = next(((when, new) for when, new in by_old.get(symbol, []) if when > since), None)
        return symbol if move is None else final(move[1], move[0])

    rows = [(old, when, final(new, when)) for old, moves in by_old.items() for when, new in moves]
    return pl.DataFrame(rows, schema=["symbol", "until", "canonical"], orient="row").sort("until")


def relabel(frame: pl.DataFrame, breaks: pl.DataFrame) -> pl.DataFrame:
    """Replace `symbol` by its canonical symbol (frame needs `symbol` and `date`)."""
    return to_canonical(frame, breaks).drop("symbol").rename({"canonical": "symbol"})


def to_canonical(frame: pl.DataFrame, breaks: pl.DataFrame) -> pl.DataFrame:
    """Add `canonical` to a frame with `symbol` and `date` columns (output sorted by date)."""
    return (
        frame.sort("date")
        .join_asof(
            breaks,
            left_on="date",
            right_on="until",
            by="symbol",
            strategy="forward",
            allow_exact_matches=False,
            check_sortedness=False,  # both sides are sorted on their date keys above
        )
        .with_columns(canonical=pl.coalesce("canonical", "symbol"))
        .drop("until")
    )
