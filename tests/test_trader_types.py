from datetime import date, timedelta

import polars as pl

from impact.flows import trader_types
from impact.flows.disclosures import normalize_client

D = date(2024, 6, 3)
NO_DICTIONARY = pl.DataFrame(schema={"client_norm": pl.String, "dict_code": pl.String})
NO_PROMOTERS = pl.DataFrame(schema={"symbol": pl.String, "client_norm": pl.String})


def client_days(rows):
    """rows: (client, symbol, security, buy_qty, sell_qty[, date])."""
    frame = pl.DataFrame(
        [(r[0], r[1], r[2], r[3], r[4], r[5] if len(r) > 5 else D) for r in rows],
        schema=["client", "symbol", "security", "buy_qty", "sell_qty", "date"],
        orient="row",
    )
    return frame.with_columns(
        client_norm=normalize_client(pl.col("client")),
        net=pl.col("buy_qty") - pl.col("sell_qty"),
        gross=pl.col("buy_qty") + pl.col("sell_qty"),
    )


def typed(rows, promoters=NO_PROMOTERS, dictionary=NO_DICTIONARY):
    out = trader_types.classify(client_days(rows), promoters, dictionary)
    return {(c, s): f"{t}:{sub}" for c, s, t, sub in out.select("client", "symbol", "ctype", "subtype").rows()}


def test_name_patterns_and_precedence():
    types = typed(
        [
            ("SBI Mutual Fund", "XYZ", "Xyz Ltd", 100, 0),
            ("Life Insurance Corporation of India", "XYZ", "Xyz Ltd", 100, 0),
            ("Norges Bank on account of the Government Pension Fund Global", "XYZ", "Xyz Ltd", 100, 0),
            ("Goldman Sachs (Singapore) Pte", "XYZ", "Xyz Ltd", 100, 0),
            ("BNP Paribas Arbitrage", "XYZ", "Xyz Ltd", 100, 0),
            ("Route One Fund I LP", "XYZ", "Xyz Ltd", 100, 0),
            ("Ramesh Kumar Shah HUF", "XYZ", "Xyz Ltd", 100, 0),
            ("Abc Trading Pvt Ltd", "XYZ", "Xyz Ltd", 100, 0),
            ("L P Sharma", "XYZ", "Xyz Ltd", 100, 0),
            ("HDFC Mutual Fund", "HDFCBANK", "HDFC Bank Limited", 100, 0),
            ("Tata Sons Ltd", "TATASTEEL", "Tata Steel Limited", 100, 0),
            ("GlaxoSmithKline Pte Ltd", "GLAXO", "GlaxoSmithKline Pharmaceuticals Limited", 0, 100),
        ]
    )
    assert types[("SBI Mutual Fund", "XYZ")] == "I:mutual_fund"
    assert types[("Life Insurance Corporation of India", "XYZ")] == "I:insurer"
    assert types[("Norges Bank on account of the Government Pension Fund Global", "XYZ")] == "I:sovereign"
    assert types[("Goldman Sachs (Singapore) Pte", "XYZ")] == "I:fpi"
    assert types[("BNP Paribas Arbitrage", "XYZ")] == "P:prop_desk"
    assert types[("Route One Fund I LP", "XYZ")] == "I:fpi"
    assert types[("Ramesh Kumar Shah HUF", "XYZ")] == "R:huf"
    assert types[("Abc Trading Pvt Ltd", "XYZ")] == "R:corporate"
    assert types[("L P Sharma", "XYZ")] == "R:individual"
    assert types[("HDFC Mutual Fund", "HDFCBANK")] == "I:mutual_fund"  # pooled beats strategic
    assert types[("Tata Sons Ltd", "TATASTEEL")] == "S:strategic"
    assert types[("GlaxoSmithKline Pte Ltd", "GLAXO")] == "S:strategic"  # strategic beats foreign


def test_patterns_fixed_after_the_first_audit():
    types = typed(
        [
            ("Bajaj Allianz Life InsuranceCompany Limited", "XYZ", "Xyz Ltd", 100, 0),
            ("HDFC MUTUAL FUN", "XYZ", "Xyz Ltd", 100, 0),
            ("Emerging Markets Mgt L.L.C The EMM Umbrella Funds", "XYZ", "Xyz Ltd", 100, 0),
            ("Hermes IF Hermes Gl Emg Mkts Fd", "XYZ", "Xyz Ltd", 100, 0),
            ("Quant Money Managers Limited", "XYZ", "Xyz Ltd", 100, 0),
            ("Capital Research and Management Company A/C Capit", "XYZ", "Xyz Ltd", 100, 0),
            ("EARC Trust SC 254", "XYZ", "Xyz Ltd", 0, 100),
            ("Free India Assurance Ser. Ltd.", "XYZ", "Xyz Ltd", 100, 0),
        ]
    )
    assert types[("Bajaj Allianz Life InsuranceCompany Limited", "XYZ")] == "I:insurer"
    assert types[("HDFC MUTUAL FUN", "XYZ")] == "I:mutual_fund"
    assert types[("Emerging Markets Mgt L.L.C The EMM Umbrella Funds", "XYZ")] == "I:fpi"
    assert types[("Hermes IF Hermes Gl Emg Mkts Fd", "XYZ")] == "I:fund"
    assert types[("Quant Money Managers Limited", "XYZ")] == "I:mutual_fund"
    assert types[("Capital Research and Management Company A/C Capit", "XYZ")] == "I:fpi"
    assert types[("EARC Trust SC 254", "XYZ")] == "I:bank"
    assert types[("Free India Assurance Ser. Ltd.", "XYZ")] == "R:corporate"


def test_patterns_fixed_after_the_second_audit():
    types = typed(
        [
            ("Jupiter India Fund382-Fund382", "XYZ", "Xyz Ltd", 100, 0),
            ("TheNomuraTrustAndBankingCo.LtdAsTheTrusteeOfNomuraIndiaStockMotherFund", "XYZ", "Xyz Ltd", 100, 0),
            ("University of Notre Dame du Lac", "XYZ", "Xyz Ltd", 100, 0),
            ("Daily Active Emerging Markets Securities Lending Common Tr", "XYZ", "Xyz Ltd", 100, 0),
            ("Norwest Venture Partners FVCI Mauritius", "XYZ", "Xyz Ltd", 0, 100),
            ("Lloyd George Investment Management (Bermuda) Ltd A/C Asian S", "XYZ", "Xyz Ltd", 100, 0),
        ]
    )
    assert types[("Jupiter India Fund382-Fund382", "XYZ")] == "I:fund"
    assert types[("TheNomuraTrustAndBankingCo.LtdAsTheTrusteeOfNomuraIndiaStockMotherFund", "XYZ")] == "I:mutual_fund"
    assert types[("University of Notre Dame du Lac", "XYZ")] == "I:endowment"
    assert types[("Daily Active Emerging Markets Securities Lending Common Tr", "XYZ")] == "I:fund"
    assert types[("Norwest Venture Partners FVCI Mauritius", "XYZ")] == "S:sponsor"
    assert types[("Lloyd George Investment Management (Bermuda) Ltd A/C Asian S", "XYZ")] == "I:manager"


def test_patterns_fixed_after_the_third_audit():
    insiders = pl.DataFrame({"symbol": ["SBFC"], "client_norm": ["ARPWOOD PARTNERS INVESTMENT ADVISORS LLP"]})
    types = typed(
        [
            ("KKR Mauritius PE Investments II Ltd", "CDEL", "Coffee Day Enterprise Ltd", 0, 100),
            ("PGGM World Equity BV", "XYZ", "Xyz Ltd", 100, 0),
            ("Quest Investment Advisors Private Limited", "XYZ", "Xyz Ltd", 100, 0),
            ("Medak SKS Mutual Benefit Trust", "SKSMICRO", "SKS Microfinance Ltd", 0, 100),
            ("Arpwood Partners Investment Advisors LLP", "SBFC", "SBFC Finance Limited", 0, 100),
            ("Sri Balaji Traders", "SRIX", "Sri Lakshmi Mills Ltd", 100, 0),
        ],
        insiders,
    )
    assert types[("KKR Mauritius PE Investments II Ltd", "CDEL")] == "S:sponsor"
    assert types[("PGGM World Equity BV", "XYZ")] == "I:fpi"
    assert types[("Quest Investment Advisors Private Limited", "XYZ")] == "I:manager"
    assert types[("Medak SKS Mutual Benefit Trust", "SKSMICRO")] == "S:strategic"  # three-letter issuer token
    assert types[("Arpwood Partners Investment Advisors LLP", "SBFC")] == "S:strategic"  # insider beats manager
    assert types[("Sri Balaji Traders", "SRIX")] == "R:individual"  # SRI is a stop word


def test_patterns_fixed_after_the_fourth_audit():
    types = typed(
        [
            ("iShares Emerging Markets Dividend ETF", "XYZ", "Xyz Ltd", 100, 0),
            ("Ruane Cunniff and Goldfard Inc A/C Acacia Institutional Par", "XYZ", "Xyz Ltd", 100, 0),
            ("FTMF A/C Franklin  Tempelton Prima Plus", "XYZ", "Xyz Ltd", 100, 0),
            ("Unit Trust of India Invesadvisory Services Limited", "XYZ", "Xyz Ltd", 100, 0),
            ("PCV Lux SCA", "XYZ", "Xyz Ltd", 100, 0),
            ("Blackstone Asia Advisors LL.. A/C A/C India Fund Inc", "XYZ", "Xyz Ltd", 0, 100),
            ("True North Fund V LLP", "HOMEFIRST", "Home First Fin Co Ind Ltd", 0, 100),
        ]
    )
    assert types[("iShares Emerging Markets Dividend ETF", "XYZ")] == "I:fund"
    assert types[("Ruane Cunniff and Goldfard Inc A/C Acacia Institutional Par", "XYZ")] == "I:fpi"
    assert types[("FTMF A/C Franklin  Tempelton Prima Plus", "XYZ")] == "I:mutual_fund"
    assert types[("Unit Trust of India Invesadvisory Services Limited", "XYZ")] == "I:mutual_fund"
    assert types[("PCV Lux SCA", "XYZ")] == "I:fpi"
    assert types[("Blackstone Asia Advisors LL.. A/C A/C India Fund Inc", "XYZ")] == "I:fpi"
    assert types[("True North Fund V LLP", "HOMEFIRST")] == "S:sponsor"


def test_patterns_fixed_after_the_final_audit():
    types = typed(
        [
            ("Cresta FundLimited", "XYZ", "Xyz Ltd", 100, 0),
            ("Driehaus EMSCGF a Series of DRIEMF", "XYZ", "Xyz Ltd", 100, 0),
            ("Standard Chartered Pvt Eq Mauritius II Ltd", "XYZ", "Xyz Ltd", 0, 100),
            ("Abc Private Equity Fund I", "XYZ", "Xyz Ltd", 0, 100),  # normalized to PVT EQUITY
        ]
    )
    assert types[("Cresta FundLimited", "XYZ")] == "I:fund"
    assert types[("Driehaus EMSCGF a Series of DRIEMF", "XYZ")] == "I:fund"
    assert types[("Standard Chartered Pvt Eq Mauritius II Ltd", "XYZ")] == "S:sponsor"
    assert types[("Abc Private Equity Fund I", "XYZ")] == "S:sponsor"


def test_insiders_match_across_word_order_middle_names_and_rare_family_names():
    insiders = pl.DataFrame(
        {
            "symbol": ["LAURUS", "DYNAMATIC", "NUCLEUS", "ABC"],
            "client_norm": ["CHAVA NAGARANI", "UDAYANT MALHOUTRA", "VISHNU DUSAD", "ANIL SHAH"],
        }
    )
    common = [(f"PERSON{i} SHAH", "Q", "Q Ltd", 100, 0) for i in range(40)]  # makes SHAH a common surname
    types = typed(
        [
            ("Nagarani Chava", "LAURUS", "Laurus Labs Limited", 100, 0),
            ("Udayant Jayant Malhoutra", "DYNAMATIC", "Dynamatic Tech. Ltd.", 100, 0),
            ("Madhu Dusad", "NUCLEUS", "Nucleus Software Exports", 100, 0),
            ("Madhu Dusad", "OTHER", "Other Ltd", 100, 0),  # family match is stock-specific
            ("Ram Shah", "ABC", "Abc Ltd", 100, 0),
            *common,
        ],
        insiders,
    )
    assert types[("Nagarani Chava", "LAURUS")] == "S:strategic"
    assert types[("Udayant Jayant Malhoutra", "DYNAMATIC")] == "S:strategic"
    assert types[("Madhu Dusad", "NUCLEUS")] == "S:strategic"
    assert types[("Madhu Dusad", "OTHER")] == "R:individual"
    assert types[("Ram Shah", "ABC")] == "R:individual"


def test_pit_promoter_and_dictionary_override():
    promoters = pl.DataFrame({"symbol": ["XYZ"], "client_norm": ["MEENA GUPTA"]})
    dictionary = pl.DataFrame({"client_norm": ["KAYAK INVESTMENTS HOLDING PTE LTD"], "dict_code": ["S:sponsor"]})
    types = typed(
        [("Meena Gupta", "XYZ", "Xyz Ltd", 100, 0), ("Kayak Investments Holding Pte Ltd", "XYZ", "Xyz Ltd", 0, 100)],
        promoters,
        dictionary,
    )
    assert types[("Meena Gupta", "XYZ")] == "S:strategic"
    assert types[("Kayak Investments Holding Pte Ltd", "XYZ")] == "S:sponsor"


def test_churner_needs_enough_two_sided_history_before_the_day():
    history = [("Quick Trader", f"S{i}", "Other Ltd", 100, 100, D - timedelta(days=10 + i)) for i in range(5)]
    today = [("Quick Trader", "XYZ", "Xyz Ltd", 100, 0), ("Once Trader", "XYZ", "Xyz Ltd", 100, 0)]
    out = trader_types.classify(client_days(history + today), NO_PROMOTERS, NO_DICTIONARY).filter(pl.col("date") == D)
    rows = {c: (t, days, share) for c, t, days, share in out.select("client", "ctype", "prior_days", "prior_two_sided").rows()}
    assert rows["Quick Trader"] == ("P", 5, 1.0)
    assert rows["Once Trader"] == ("R", 0, None)


def test_dictionary_file_is_well_formed():
    dictionary = trader_types.read_dictionary()
    assert dictionary.height > 0
    assert dictionary["dict_code"].str.split(":").list.first().is_in(["I", "R", "P", "S"]).all()
