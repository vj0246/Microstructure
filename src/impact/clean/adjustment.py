"""Split and bonus adjustment.

NSE's legacy bhavcopy does not adjust PREVCLOSE on ex-dates (IVC 2009-09-16, GUJRATGAS 2009-09-17),
so factors come from the corporate-action subject text. Each is placed on the symbol's first trading
day on or after its ex-date and applied only when the overnight jump there confirms it; the rest are
kept for audit (M1 gate)."""

import math
import re

import polars as pl

PRICE_COLUMNS = ("open", "high", "low", "close", "last", "prev_close")
SHARE_COLUMNS = ("volume",)
MAX_LOG_MISS = 0.2  # a confirming jump lies within 20% (in logs) of the factor
AMBIGUOUS_LOG = 0.3  # factors this close to 1 cannot be told from an ordinary overnight move

# Old records abbreviate: "Fv Spl-Rs10tore1/Bon-1:1", "Bon 5:1purpose Revised"; "to" may touch digits.
_BONUS = re.compile(r"\bbon(?:us)?(?: issue| shares in the ratio of)?\s*-?\s*(\d+)\s*:\s*(\d+)", re.I)
_SPLIT = re.compile(
    r"(?:split|fv\s*spl|sub-division|consolidat\w*).*?(?:from\s*)?(?:rs\.?|re\.?)?\s*(\d+(?:\.\d+)?)\s*(?:/-)?"
    r".*?(?<![a-z])to\s*(?:face value\s*)?(?:rs\.?|re\.?)?\s*(\d+(?:\.\d+)?)",
    re.I,
)
_NOT_EQUITY = re.compile(r"debenture|preference|ncrps|dvr", re.I)
_ARRANGEMENT = r"(?i)arrangement|demerger|reduction"  # price-moving actions with no ratio in the text
AUDIT_COLUMNS = ["symbol", "date", "factor", "source", "trade_date", "prev", "open", "jump", "confirmed", "apply"]


def subject_parts(subject: str) -> dict[str, float | None]:
    """Price divisors of the bonus (a:b means a new per b held) and face-value change in a subject."""
    if _NOT_EQUITY.search(subject):
        return {"bonus": None, "split": None}
    bonus, split = _BONUS.search(subject), _SPLIT.search(subject)
    return {
        "bonus": (int(bonus[1]) + int(bonus[2])) / int(bonus[2]) if bonus else None,
        "split": float(split[1]) / float(split[2]) if split else None,
    }


def subject_factor(subject: str) -> float | None:
    """Combined price divisor of a subject; None when it changes nothing."""
    parts = [p for p in subject_parts(subject).values() if p is not None]
    factor = math.prod(parts)
    return None if not parts or factor == 1.0 else factor


def action_factors(actions: pl.DataFrame) -> pl.DataFrame:
    """(symbol, date, factor) from records with symbol, date (the ex-date), subject. Within a part (bonus
    or split) repeated records of one event (revised purposes) count once; bonus and split filed as
    separate records on one ex-date multiply."""
    parts = pl.Struct({"bonus": pl.Float64, "split": pl.Float64})
    return (
        actions.with_columns(p=pl.col("subject").map_elements(subject_parts, return_dtype=parts))
        .unnest("p")
        .group_by("symbol", "date")
        .agg(pl.col("bonus").drop_nulls().unique().product(), pl.col("split").drop_nulls().unique().product())
        .with_columns(factor=pl.col("bonus") * pl.col("split"))
        .filter(pl.col("factor") != 1.0)
        .select("symbol", "date", "factor")
        .sort("symbol", "date")
    )


def _place(rows: pl.DataFrame, dated: pl.DataFrame) -> pl.DataFrame:
    """Move each (symbol, date) to the symbol's first trading day within 10 days on or after it
    (`trade_date`) and add `jump` = previous close / that day's open. `rows`: trading rows only."""
    trading = (
        rows.sort("symbol", "date")
        .with_columns(prev=pl.col("close").shift(1).over("symbol"))
        .select("symbol", pl.col("date").alias("trade_date"), "prev", "open")
        .sort("trade_date")
    )
    return (
        dated.sort("date")
        .join_asof(
            trading,
            left_on="date",
            right_on="trade_date",
            by="symbol",
            strategy="forward",
            tolerance="10d",
            check_sortedness=False,  # both sides sorted on their date keys above
        )
        .with_columns(jump=pl.col("prev") / pl.col("open"))
    )


def confirm(rows: pl.DataFrame, factors: pl.DataFrame) -> pl.DataFrame:
    """Audit rows for text-derived factors. `confirmed` when the ex-date jump is closer (in logs) to the
    factor than to no change, and within MAX_LOG_MISS of it. `apply` adds factors too small to test
    (|ln f| < AMBIGUOUS_LOG), whose record date is trusted; testable factors that fail stay out."""
    miss = (pl.col("jump") / pl.col("factor")).log().abs()
    return (
        _place(rows, factors)
        .with_columns(
            source=pl.lit("subject"),
            confirmed=((miss < pl.col("jump").log().abs()) & (miss < MAX_LOG_MISS)).fill_null(False),
        )
        .with_columns(
            apply=pl.col("confirmed")
            | ((pl.col("factor").log().abs() < AMBIGUOUS_LOG) & pl.col("trade_date").is_not_null())
        )
        .select(AUDIT_COLUMNS)
    )


def arrangement_factors(rows: pl.DataFrame, actions: pl.DataFrame, factors: pl.DataFrame) -> pl.DataFrame:
    """Audit rows for schemes of arrangement, demergers and capital reductions, whose subjects carry no
    ratio: when one goes ex with a clear gap (|ln jump| >= AMBIGUOUS_LOG) the observed jump is the
    factor. That day's own overnight return is lost; events next to ex-dates are filtered anyway.
    Stock-days that already have a text-derived factor are skipped."""
    dated = (
        actions.filter(pl.col("subject").str.contains(_ARRANGEMENT))
        .select("symbol", "date")
        .unique()
        .join(factors, on=["symbol", "date"], how="anti")
    )
    clear = (pl.col("jump").log().abs() >= AMBIGUOUS_LOG).fill_null(False)
    return (
        _place(rows, dated)
        .with_columns(factor=pl.col("jump"), source=pl.lit("jump"), confirmed=clear, apply=clear)
        .select(AUDIT_COLUMNS)
    )


def apply_factors(panel: pl.DataFrame, factors: pl.DataFrame) -> pl.DataFrame:
    """Back-adjust to latest share units with (symbol, date, factor) rows, at most one per symbol-day.
    `cum` = product of factors strictly after the row's date: prices divide by it and share counts
    multiply by it; deal quantities must use the same `cum`."""
    df = (
        panel.join(factors, on=["symbol", "date"], how="left")
        .with_columns(pl.col("factor").fill_null(1.0))
        .sort("symbol", "date")
        .with_columns(cum=pl.col("factor").cum_prod(reverse=True).shift(-1, fill_value=1.0).over("symbol"))
    )
    return df.with_columns(
        *(pl.col(c) / pl.col("cum") for c in PRICE_COLUMNS if c in df.columns),
        *(pl.col(c) * pl.col("cum") for c in SHARE_COLUMNS if c in df.columns),
    )
