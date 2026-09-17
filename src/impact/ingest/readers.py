"""Raw NSE files to typed polars frames. Dates come from file names: one trading day per file."""

import io
import json
import zipfile
from collections.abc import Callable, Iterable
from datetime import date, datetime
from pathlib import Path

import polars as pl

BOM = "\ufeff"
DEAL_COLUMNS = {
    "Date": "date",
    "Symbol": "symbol",
    "Security Name": "security",
    "Client Name": "client",
    "Buy / Sell": "side",
    "Quantity Traded": "qty",
    "Trade Price / Wght. Avg. Price": "price",
    "Remarks": "remarks",
}
SIDES = {"BUY": "BUY", "B": "BUY", "SELL": "SELL", "S": "SELL", "SOLD": "SELL"}  # labels seen 2004-2026
# One schema for both bhavcopy eras; legacy files before ~2011 lack trades and ISIN (left null).
LEGACY_BHAV = {
    "SYMBOL": "symbol",
    "SERIES": "series",
    "OPEN": "open",
    "HIGH": "high",
    "LOW": "low",
    "CLOSE": "close",
    "LAST": "last",
    "PREVCLOSE": "prev_close",
    "TOTTRDQTY": "volume",
    "TOTTRDVAL": "value",
    "TOTALTRADES": "trades",
    "ISIN": "isin",
}
UDIFF_BHAV = {
    "TckrSymb": "symbol",
    "SctySrs": "series",
    "OpnPric": "open",
    "HghPric": "high",
    "LwPric": "low",
    "ClsPric": "close",
    "LastPric": "last",
    "PrvsClsgPric": "prev_close",
    "TtlTradgVol": "volume",
    "TtlTrfVal": "value",
    "TtlNbOfTxsExctd": "trades",
    "ISIN": "isin",
}
PRICE_FLOATS = ("open", "high", "low", "close", "last", "prev_close", "value")


def _read_table(source: Path | bytes) -> pl.DataFrame:
    """All-string CSV with stripped header names and values."""
    raw = pl.read_csv(io.BytesIO(source) if isinstance(source, bytes) else source, infer_schema=False, encoding="utf8-lossy")
    raw = raw.rename({c: c.lstrip(BOM).strip() for c in raw.columns})
    return raw.with_columns(pl.col(pl.String).str.strip_chars())


def _select(raw: pl.DataFrame, names: dict[str, str], required: set[str], label: str) -> pl.DataFrame:
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(f"{label}: missing columns {sorted(missing)}")
    present = {k: v for k, v in names.items() if k in raw.columns}
    absent = [pl.lit(None, pl.String).alias(v) for k, v in names.items() if k not in raw.columns]
    return raw.select(list(present)).rename(present).with_columns(absent)


def read_all(paths: Iterable[Path], reader: Callable[[Path], pl.DataFrame]) -> pl.DataFrame:
    frames = [reader(path) for path in paths]
    if not frames:
        raise FileNotFoundError(f"no raw files for {reader.__name__}")
    return pl.concat(frames)


def deal_year_files(folder: Path) -> dict[int, Path]:
    """Per year, the widest window starting 1 January among files named <kind>_<start>_<end>.csv."""
    widest: dict[int, tuple[date, Path]] = {}
    for path in folder.glob("*.csv"):
        start, end = (date.fromisoformat(part) for part in path.stem.rsplit("_", 2)[1:])
        if (start.month, start.day) == (1, 1) and end > widest.get(start.year, (date.min, path))[0]:
            widest[start.year] = (end, path)
    return {year: path for year, (_, path) in sorted(widest.items())}


def file_day(path: Path) -> date:
    """Trading day in a daily file name: legacy or UDiFF bhavcopy, MTO, sec_bhavdata_full, band changes."""
    if path.name.startswith("cm"):
        return datetime.strptime(path.name[2:11], "%d%b%Y").date()
    if path.name.startswith("BhavCopy"):
        return datetime.strptime(path.name.split("_")[6], "%Y%m%d").date()
    if path.name.startswith("MTO_"):
        return datetime.strptime(path.stem[4:12], "%d%m%Y").date()
    if path.name.startswith("PR"):
        return datetime.strptime(path.stem[2:8], "%d%m%y").date()
    return datetime.strptime(path.stem[-8:], "%d%m%Y").date()


def _only_member(path: Path) -> bytes:
    with zipfile.ZipFile(path) as zf:
        (name,) = zf.namelist()
        return zf.read(name)


def read_deals(path: Path) -> pl.DataFrame:
    """Bulk or block deal CSV: BOM, space-padded quoted headers, Indian digit grouping, DD-MON-YYYY."""
    raw = _read_table(path)
    raw = raw.rename({"Buy/Sell": "Buy / Sell"}) if "Buy/Sell" in raw.columns else raw  # live bulk.csv header
    df = _select(raw, DEAL_COLUMNS, set(DEAL_COLUMNS), path.name)
    return df.with_columns(
        pl.col("date").str.to_titlecase().str.to_date("%d-%b-%Y"),
        pl.col("side").str.to_uppercase().replace_strict(SIDES),  # unknown label raises
        pl.col("qty").str.replace_all(",", "").cast(pl.Int64),
        pl.col("price").str.replace_all(",", "").cast(pl.Float64),
    )


def _prices(raw: pl.DataFrame, names: dict[str, str], day: date, label: str) -> pl.DataFrame:
    required = set(names) - {"TOTALTRADES", "ISIN"}
    df = _select(raw, names, required, label)
    return df.select(
        pl.lit(day).alias("date"),
        "symbol",
        "series",
        *(pl.col(c).cast(pl.Float64) for c in PRICE_FLOATS),
        pl.col("volume").cast(pl.Float64).cast(pl.Int64),
        pl.col("trades").cast(pl.Float64).cast(pl.Int64),
        "isin",
    )


def read_legacy_bhav(path: Path) -> pl.DataFrame:
    """cmDDMONYYYYbhav.csv.zip (1995 to 2024-07-05)."""
    return _prices(_read_table(_only_member(path)), LEGACY_BHAV, file_day(path), path.name)


def read_udiff_bhav(path: Path) -> pl.DataFrame:
    """BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip (2024-01-01 onward)."""
    return _prices(_read_table(_only_member(path)), UDIFF_BHAV, file_day(path), path.name)


def read_sec_full(path: Path) -> pl.DataFrame:
    """sec_bhavdata_full_DDMMYYYY.csv: VWAP and delivery; "-" means not reported."""
    day = file_day(path)
    names = {"SYMBOL": "symbol", "SERIES": "series", "TTL_TRD_QNTY": "volume", "AVG_PRICE": "avg_price", "DELIV_QTY": "deliv_qty"}
    df = _select(_read_table(path), names, set(names), path.name)
    return df.select(
        pl.lit(day).alias("date"),
        "symbol",
        "series",
        pl.col("volume").cast(pl.Int64),
        pl.col("avg_price").cast(pl.Float64),
        pl.col("deliv_qty").cast(pl.Int64, strict=False),
    )


def read_mto(path: Path) -> pl.DataFrame:
    """MTO_DDMMYYYY.DAT: preamble lines, then records `20,sr,SYMBOL,SERIES,traded,deliverable,pct`."""
    day = file_day(path)
    rows = [line.split(",")[2:6] for line in path.read_text(encoding="latin-1").splitlines() if line.startswith("20,")]
    df = pl.DataFrame(rows, schema=["symbol", "series", "volume", "deliv_qty"], orient="row")
    return df.select(
        pl.lit(day).alias("date"),
        pl.col("symbol").str.strip_chars(),
        pl.col("series").str.strip_chars(),
        pl.col("volume").cast(pl.Int64),
        pl.col("deliv_qty").cast(pl.Int64, strict=False),
    )


def _json_records(paths: Iterable[Path]) -> list[dict[str, object]]:
    """Records of monthly API files: a bare list, or a dict with the list under "data"."""
    records = []
    for path in paths:
        data = json.loads(path.read_bytes())
        records += data["data"] if isinstance(data, dict) else data
    return records


def read_board_meetings(paths: Iterable[Path]) -> pl.DataFrame:
    """Monthly corporate-board-meetings JSON -> (symbol, date) of each meeting."""
    return (
        pl.DataFrame(_json_records(paths), infer_schema_length=None)
        .select(
            pl.col("bm_symbol").str.strip_chars().alias("symbol"),
            pl.col("bm_date").str.to_date("%d-%b-%Y", strict=False).alias("date"),
        )
        .drop_nulls()
        .unique()
    )


def read_results(paths: Iterable[Path]) -> pl.DataFrame:
    """Monthly corporates-financial-results JSON -> (symbol, broadcast timestamp)."""
    return (
        pl.DataFrame(_json_records(paths), infer_schema_length=None)
        .select(
            pl.col("symbol").str.strip_chars(),
            pl.col("broadCastDate").str.to_datetime("%d-%b-%Y %H:%M:%S", strict=False).alias("broadcast"),
        )
        .drop_nulls()
        .unique()
    )


def read_corporate_actions(paths: Iterable[Path]) -> pl.DataFrame:
    """Monthly corporateActions JSON -> (symbol, series, ex_date, subject); records without an ex-date dropped."""
    records = _json_records(paths)
    return (
        pl.DataFrame(records, infer_schema_length=None)
        .select(
            pl.col("symbol").str.strip_chars(),
            pl.col("series").str.strip_chars(),
            pl.col("exDate").str.to_date("%d-%b-%Y", strict=False).alias("ex_date"),
            pl.col("subject").str.strip_chars(),
        )
        .drop_nulls("ex_date")
        .unique()
        .sort("ex_date", "symbol")
    )


def read_pit(paths: Iterable[Path]) -> pl.DataFrame:
    """Monthly corporates-pit JSON (insider trades, SEBI PIT Reg 7(2)) -> symbol, name, category,
    intimation date."""
    return (
        pl.DataFrame(_json_records(paths), infer_schema_length=None)
        .select(
            pl.col("symbol").str.strip_chars(),
            pl.col("acqName").str.strip_chars().alias("name"),
            pl.col("personCategory").str.strip_chars().alias("category"),
            pl.col("intimDt").str.to_date("%d-%b-%Y", strict=False).alias("date"),
        )
        .unique()
    )


def read_band_hits(path: Path) -> pl.DataFrame:
    """NSE's band-hit list (the bh file inside a PRDDMMYY.zip): date, symbol, series, side (+1 upper, -1 lower)."""
    schema = {"date": pl.Date, "symbol": pl.String, "series": pl.String, "side": pl.Int8}
    with zipfile.ZipFile(path) as zf:
        member = next((n for n in zf.namelist() if n.lower().startswith("bh")), None)
        if member is None:
            return pl.DataFrame(schema=schema)
        table = _read_table(zf.read(member))
    if "SERIES" in table.columns:  # files before March 2012 call the series column "SR"
        table = table.rename({"SERIES": "SR"})
    names = {"SYMBOL": "symbol", "SR": "series", "HIGH/LOW": "side"}
    return _select(table, names, set(names), member).select(
        pl.lit(file_day(path)).alias("date"),
        "symbol",
        "series",
        pl.col("side").replace_strict({"H": 1, "L": -1}, return_dtype=pl.Int8),
    )


def read_band_changes(path: Path) -> pl.DataFrame:
    """eq_band_changes_DDMMYYYY.csv: price band (percent) moves effective that day; non-numeric bands become null."""
    day = file_day(path)
    names = {"Symbol": "symbol", "Series": "series", "From": "band_from", "To": "band_to"}
    df = _select(_read_table(path), names, set(names), path.name)
    return df.select(
        pl.lit(day).alias("date"),
        "symbol",
        "series",
        pl.col("band_from").cast(pl.Float64, strict=False),
        pl.col("band_to").cast(pl.Float64, strict=False),
    )
