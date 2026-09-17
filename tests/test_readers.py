"""Golden rows copied from real NSE files of each format era."""

import json
import zipfile
from datetime import date

import polars as pl
import pytest

from impact.ingest import readers
from impact.ingest.readers import BOM

DEALS_HEADER = (
    BOM + '"Date ","Symbol ","Security Name ","Client Name ","Buy / Sell ","Quantity Traded ",'
    '"Trade Price / Wght. Avg. Price ","Remarks "\n'
)


def zipped(tmp_path, name, text):
    path = tmp_path / f"{name}.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(name, text)
    return path


def test_read_deals_handles_bom_padding_and_indian_grouping(tmp_path):
    path = tmp_path / "bulk.csv"
    path.write_bytes(
        (
            DEALS_HEADER
            + '"01-JAN-2024","3IINFOLTD","3i Infotech Limited","GRAVITON RESEARCH CAPITAL LLP","SELL",'
            '"14,17,288","51.63","-"\n'
            '"05-JAN-2024","INFIBEAM","Infibeam Avenues Limited","SONAL DESAI ","BUY","45,00,000","1,022.80",\n'
        ).encode("utf-8")
    )
    df = readers.read_deals(path)
    assert df.columns == ["date", "symbol", "security", "client", "side", "qty", "price", "remarks"]
    assert df["date"].to_list() == [date(2024, 1, 1), date(2024, 1, 5)]
    assert df["qty"].to_list() == [1_417_288, 4_500_000]
    assert df["price"].to_list() == [51.63, 1022.80]
    assert df["client"].to_list() == ["GRAVITON RESEARCH CAPITAL LLP", "SONAL DESAI"]


def test_read_deals_accepts_live_bulk_csv_format(tmp_path):
    path = tmp_path / "bulk_snapshot.csv"
    path.write_text(
        "Date,Symbol,Security Name,Client Name,Buy/Sell,Quantity Traded,Trade Price / Wght. Avg. Price,Remarks\n"
        "11-SEP-2026,AEROPLANE,Amir Chand Jag Kum (E) L,NITIN KUMAR NIPUN KUMAR HUF,BUY,1276148,201.45,-\n",
        encoding="utf-8",
    )
    row = readers.read_deals(path).row(0, named=True)
    assert (row["date"], row["side"], row["qty"], row["price"]) == (date(2026, 9, 11), "BUY", 1276148, 201.45)


def test_read_deals_normalizes_side_labels_and_rejects_unknown(tmp_path):
    row = '"02-JAN-2012","ABC","Abc Ltd","X","{}","1,000","10.00","-"\n'
    path = tmp_path / "block.csv"
    path.write_text(DEALS_HEADER + "".join(row.format(s) for s in ("B", "S", "SOLD", "buy")), encoding="utf-8")
    assert readers.read_deals(path)["side"].to_list() == ["BUY", "SELL", "SELL", "BUY"]
    path.write_text(DEALS_HEADER + row.format("HOLD"), encoding="utf-8")
    with pytest.raises(pl.exceptions.InvalidOperationError):
        readers.read_deals(path)


def test_legacy_bhav_2010_without_trades_and_isin(tmp_path):
    path = zipped(
        tmp_path,
        "cm04JAN2010bhav.csv",
        "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,\n"
        "20MICRONS,EQ,46,47.9,45.7,47.55,47.5,46.05,36282,1719201.35,4-JAN-2010,\n",
    )
    row = readers.read_legacy_bhav(path).row(0, named=True)
    assert row["date"] == date(2010, 1, 4)
    assert (row["open"], row["close"], row["last"], row["prev_close"]) == (46.0, 47.55, 47.5, 46.05)
    assert (row["volume"], row["value"], row["trades"], row["isin"]) == (36282, 1719201.35, None, None)


def test_legacy_bhav_2012_and_udiff_share_one_schema(tmp_path):
    legacy = readers.read_legacy_bhav(
        zipped(
            tmp_path,
            "cm02JAN2012bhav.csv",
            "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN,\n"
            "20MICRONS,EQ,62.05,63.9,61.4,62.5,63.5,62.25,70533,4393274.15,02-JAN-2012,621,INE144J01019,\n",
        )
    )
    udiff = readers.read_udiff_bhav(
        zipped(
            tmp_path,
            "BhavCopy_NSE_CM_0_0_0_20240708_F_0000.csv",
            "TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,FininstrmActlXpryDt,"
            "StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,"
            "SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,"
            "Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4\n"
            "2024-07-08,2024-07-08,CM,NSE,STK,20092,INE488B01017,TASTYBITE,EQ,,,,,TASTY BITE EATABLES LTD,"
            "10400.10,10534.50,10191.00,10273.20,10299.95,10374.25,,10272.10,,,2793,28877900.75,1046,F1,1,,,,,\n",
        )
    )
    assert legacy.schema == udiff.schema
    assert legacy.row(0, named=True)["trades"] == 621 and legacy.row(0, named=True)["isin"] == "INE144J01019"
    row = udiff.row(0, named=True)
    assert (row["date"], row["symbol"], row["series"]) == (date(2024, 7, 8), "TASTYBITE", "EQ")
    assert (row["last"], row["prev_close"], row["volume"], row["trades"]) == (10299.95, 10374.25, 2793, 1046)


def test_sec_full_strips_padding_and_nulls_unreported_delivery(tmp_path):
    path = tmp_path / "sec_bhavdata_full_08072024.csv"
    path.write_text(
        "SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, "
        "AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER\n"
        "20MICRONS, EQ, 08-Jul-2024, 224.72, 229.99, 229.99, 221.40, 222.30, 223.75, 224.43, 223960, "
        "502.64, 9557, 83468, 37.27\n"
        "21STCENMGM, BE, 08-Jul-2024, 73.49, 74.95, 74.95, 74.95, 74.95, 74.95, 74.95, 6801, 5.10, 21, -, -\n"
    )
    df = readers.read_sec_full(path)
    assert df["symbol"].to_list() == ["20MICRONS", "21STCENMGM"]
    assert df["avg_price"].to_list() == [224.43, 74.95]
    assert df["deliv_qty"].to_list() == [83468, None]
    assert df["date"].unique().to_list() == [date(2024, 7, 8)]


def test_mto_skips_preamble(tmp_path):
    path = tmp_path / "MTO_02012012.DAT"
    path.write_text(
        "Security Wise Delivery Position - Compulsory Rolling Settlement\n"
        "10,MTO,02012012,116070724,0001495\n"
        "Trade Date <02-JAN-2012>,Settlement Type <N>,Settlement No <2012001>,Settlement Date <04-JAN-2012>\n"
        "Record Type,Sr No,Name of Security,Quantity Traded,Deliverable Quantity(gross across client level),"
        "% of Deliverable Quantity to Traded Quantity\n"
        "20,1,20MICRONS,EQ,70533,2548,3.61\n"
        "20,2,3IINFOTECH,EQ,1528247,551142,36.06\n"
    )
    df = readers.read_mto(path)
    assert df.row(1) == (date(2012, 1, 2), "3IINFOTECH", "EQ", 1528247, 551142)


def test_corporate_actions_drop_records_without_ex_date(tmp_path):
    record = {"symbol": "OIL", "series": "EQ", "exDate": "03-Jan-2012", "subject": "Interim Dividend Rs.25/- Per Share"}
    (tmp_path / "a.json").write_text(json.dumps([record, {**record, "symbol": "X", "exDate": "-"}]))
    (tmp_path / "b.json").write_text(json.dumps([record]))
    df = readers.read_corporate_actions(sorted(tmp_path.glob("*.json")))
    assert df.rows() == [("OIL", "EQ", date(2012, 1, 3), "Interim Dividend Rs.25/- Per Share")]


def test_band_hits_inside_the_pr_zip(tmp_path):
    path = tmp_path / "PR020112.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Pd020112.csv", "MKT,SERIES,SYMBOL\n")
        zf.writestr(
            "bh02012012.csv",
            "SYMBOL,SR,SECURITY,HIGH/LOW,INDEX FLAG\nAVANTI,BE,AVANTI FEEDS LIMITED,H,\nABC,EQ,Abc Ltd,L,\n",
        )
    assert readers.file_day(path) == date(2012, 1, 2)
    assert readers.read_band_hits(path).rows() == [(date(2012, 1, 2), "AVANTI", "BE", 1), (date(2012, 1, 2), "ABC", "EQ", -1)]
    later = tmp_path / "PR051225.zip"  # later files: SERIES header, no INDEX FLAG
    with zipfile.ZipFile(later, "w") as zf:
        zf.writestr("bh05122025.csv", "SYMBOL,SERIES,SECURITY,HIGH/LOW\nXYZ,EQ,Xyz Ltd,H\n")
    assert readers.read_band_hits(later).rows() == [(date(2025, 12, 5), "XYZ", "EQ", 1)]


def test_band_changes(tmp_path):
    path = tmp_path / "eq_band_changes_02012012.csv"
    path.write_text(
        "Sr. No.,Symbol,Series,Security Name,From,To\n"
        "1,PERIATEA,EQ,The Peria Karamalai Tea & Produce Company Limited,10,5\n"
        "2,GKB,EQ,GKB Ophthalmics Limited,No Band,5\n"
    )
    df = readers.read_band_changes(path)
    assert df.row(0) == (date(2012, 1, 2), "PERIATEA", "EQ", 10.0, 5.0)
    assert df["band_from"].to_list() == [10.0, None]
