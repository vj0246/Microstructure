"""Trader typing (PLAN J13): I institutional, R reference, P churner or prop desk, S strategic.

The hand dictionary in `client_types.csv` is the one manual input: the author assigned those types
from recollection or from the name, never from a filing. Its `evidence` column says which, and
`report.typing_sensitivity` re-runs the confirmatory tests without it.

Deterministic, first match wins: hand dictionary; prop-desk name patterns; venture and private-equity
sponsors (S); pooled institutions (sovereign, pension, insurer, mutual fund or trustee, endowment, AIF,
bank or asset-reconstruction trust); strategic for this stock; investment managers, foreign or fund
patterns; churner by behavior (reference candidates only); reference subtypes.

Strategic for a stock: a distinctive issuer token in the client name; a PIT insider of the stock
(promoter, director, KMP or relative) matched on the full name in any word order or on first plus
last name; or a client sharing a rare surname (first or last token) with such an insider. Per
client-day, `directional` marks |net| >= half of gross: only directional client-days define a
stock-day's type."""

from pathlib import Path

import polars as pl

from impact.flows.disclosures import normalize_client

DICTIONARY = Path(__file__).with_name("client_types.csv")
CHURN_SHARE = 0.5  # prior two-sided share that marks a churner
CHURN_MIN_DAYS = 5  # prior client-days needed before behavior can decide
RARE_SURNAME = 30  # a family token must appear in fewer distinct client names than this

_PROP = r"\bARBITRAGE\b|\bPRINCIPAL\b|\bPROPRIETARY\b"
_SPONSOR = (  # venture and private-equity holders: their disclosed trades are almost always pre-IPO exits
    # names are normalized first, so PRIVATE EQUITY reads PVT EQUITY (and PVT EQ when abbreviated)
    r"\bFVCI\b|\bVENTURE PARTNERS\b|\bVENTURE CAPITAL\b|\bPVT EQ(?:UITY)?\b|\bPE INVESTMENTS?\b|\bKKR\b|\bTPG\b"
    r"|\bCARLYLE\b|\bWARBURG PINCUS\b|\bBAIN CAPITAL\b|\bORBIMED\b|\bCREADOR\b|\bSEQUOIA\b"
    r"|\bNORWEST\b|\bGENERAL ATLANTIC\b|\bTRUE NORTH\b"
)  # not BLACKSTONE: its advisory arm trades for listed funds; its PE vehicles are in the dictionary
_POOLED = {  # regulated pools: beat the stock-specific strategic tag (HDFC MF buying HDFC Bank stays I)
    "sovereign": r"\bGOVERNMENT OF\b|\bSOVEREIGN\b|\bINVESTMENT AUTHORITY\b|\bNORGES BANK\b|\bMONETARY AUTHORITY\b",
    "pension": r"\bPENSION\b|\bPROVIDENT\b|\bSUPERANNUATION\b|\bRETIREMENT\b",
    "insurer": r"INSURANCE|\bASSURANCE (?:CO|COMPANY)\b|\bLIFE ASSURANCE\b",  # names glue "INSURANCECOMPANY"
    "mutual_fund": (  # truncated, glued
        r"\bMUTUAL FUN|\bMF\b|\bFTMF\b|\bASSET MANAGEMENT\b|\bAMC\b|\bMONEY MANAGERS\b|TRUSTEE"
        r"|\bUNIT TRUST OF INDIA\b|\bUTI\b"
    ),
    "endowment": r"\bUNIVERSITY\b|\bENDOWMENT\b",
    "aif": r"\bAIF\b|\bALTERNATIVE INVESTMENT\b",
    "bank": r"\bBANK\b|\bE?ARC\b|\bARCIL\b|\bASSET RECONSTRUCTION\b",  # asset-reconstruction trusts sell pledged stock
}
_OTHER_INSTITUTIONS = {  # lose to the strategic tag: a foreign parent or an insider's advisory firm is S
    "manager": r"\bINVESTMENT MANAGEMENT\b|\bINVESTMENT MGT\b|\bPMS\b|\bINVESTMENT ADVIS[OE]RS\b",
    "fpi": (
        r"\bFPI\b|\bPTE\b|\bMAURITIUS\b|\bSINGAPORE\b|\bCAYMAN\b|\bLUXEMBOURG\b|\bBERMUDA\b|\bPLC\b|\bINC\b|\bB ?V$|\bSCA\b"
        r"|\bL ?L ?C\b|\bL ?P$"
        r"|\bSICAV\b|\bGMBH\b|\bODI\b|\bGOLDMAN SACHS\b|\bMORGAN STANLEY\b|\bCITIGROUP\b|\bMERRILL LYNCH\b"
        r"|\bJ ?P ?MORGAN\b|\bNOMURA\b|\bCLSA\b|\bBARCLAYS\b|\bCREDIT SUISSE\b|\bDEUTSCHE\b|\bHSBC\b"
        r"|\bSOCIETE GENERALE\b|\bBNP PARIBAS\b|\bUBS\b|\bMACQUARIE\b|\bBLACKROCK\b|\bVANGUARD\b|\bFIDELITY\b"
        r"|\bTEMPLETON\b|\bCAPITAL RESEARCH\b"
    ),
    # "FUND382", "CRESTA FUNDLIMITED", "EMG MKTS FD", "EMSCGF A SERIES OF DRIEMF"
    "fund": r"\bFUNDS?(?:\d*|LIMITED|LTD)\b|\bFDS?\b|\bETFS?\b|\bA SERIES OF\b|\bSECURITIES LENDING\b|\bCOMMON TR",
}
_PASSIVE = r"\bETF\b|\bINDEX\b|\bNIFTY\b|\bSENSEX\b|\bEXCHANGE TRADED\b|\bVANGUARD\b|\bISHARES\b"
_HUF = r"\bHUF\b"
_TRUST = r"\bTRUST\b"
_CORPORATE = (
    r"\bLTD\b|\bLLP\b|\bPVT\b|\bCORPORATION\b|\bCORP\b|\bCO\b|\bCOMPANY\b|\bINDUSTRIES\b|\bENTERPRISES\b"
    r"|\bHOLDINGS?\b|\bVENTURES\b|\bFINANCE\b|\bSECURITIES\b|\bCAPITAL\b|\bINVESTMENTS?\b|\bTRADING\b|\bSERVICES\b"
)
# Issuer-name words too common to tie a client to one company.
_STOP = [
    "LIMITED", "INDIA", "INDIAN", "INDUSTRIES", "INDUSTRY", "CORPORATION", "COMPANY", "GROUP", "HOLDINGS",
    "HOLDING", "INVESTMENTS", "INVESTMENT", "CAPITAL", "FINANCE", "FINANCIAL", "SERVICES", "SECURITIES",
    "TRADING", "ENTERPRISES", "INTERNATIONAL", "GLOBAL", "TECHNOLOGIES", "TECHNOLOGY", "SYSTEMS", "SOLUTIONS",
    "POWER", "ENERGY", "STEEL", "INFRA", "INFRASTRUCTURE", "PROJECTS", "VENTURES", "BANK", "MUTUAL", "FUND",
    "TRUST", "PRIVATE", "PUBLIC", "NATIONAL", "CHEMICALS", "PHARMA", "PHARMACEUTICALS", "MOTORS", "AUTO",
    "TEXTILES", "MILLS", "SUGAR", "CEMENT", "PAPER", "FOODS", "AGRO", "EXPORTS", "IMPEX", "OVERSEAS",
    "MANAGEMENT", "ADVISORS", "WEALTH", "HOUSING", "DEVELOPERS", "ESTATES", "PROPERTIES", "REALTY", "MEDIA",
    "ENTERTAINMENT", "COMMUNICATIONS", "TELECOM", "ELECTRICALS", "ENGINEERING", "PRODUCTS", "RESOURCES",
    "METALS", "ALLOYS", "PLASTICS", "POLYMERS", "LOGISTICS", "HOTELS", "RESORTS", "HEALTHCARE", "LIFE",
    "SCIENCES", "BHARAT", "HINDUSTAN", "SHREE", "SHRI", "FINSERV", "LEASING", "CREDIT", "ELECTRIC", "GENERAL",
    # three-letter tokens count too (SKS, RPG, GMR), so common short words must be listed
    "LTD", "PVT", "AND", "THE", "HUF", "LLP", "INC", "PLC", "NEW", "ONE", "SRI", "RAM", "SAI", "JAI",
]


def read_dictionary(path: Path = DICTIONARY, drop_recalled: bool = False) -> pl.DataFrame:
    """Hand overrides: client_norm (normalized here too), type, subtype, evidence, note.

    `evidence` is "unverified_recall" (the author's recollection of who the client is, NOT read from any
    filing or NSE record) or "name_inference" (read off the name itself). Neither is source data.
    `drop_recalled` keeps only the name inferences, which the typing sensitivity run uses."""
    rows = pl.read_csv(path, infer_schema=False)
    if drop_recalled:
        rows = rows.filter(pl.col("evidence") != "unverified_recall")
    return (
        rows.with_columns(client_norm=normalize_client(pl.col("client_norm")))
        .select("client_norm", pl.concat_str(pl.col("type"), pl.lit(":"), pl.col("subtype")).alias("dict_code"))
        .unique("client_norm")
    )


def _chain(patterns: dict[str, str], kind: str, then: pl.Expr) -> pl.Expr:
    name = pl.col("client_norm")
    out = then
    for subtype, pattern in reversed(patterns.items()):
        out = pl.when(name.str.contains(pattern)).then(pl.lit(f"{kind}:{subtype}")).otherwise(out)
    return out


def churn_history(clients: pl.DataFrame) -> pl.DataFrame:
    """Add prior_days and prior_two_sided: the client's disclosed client-days (any stock) over the 365
    calendar days before the date, and the share of them on which it traded both sides."""
    per_day = (
        clients.group_by("client_norm", "date")
        .agg(n=pl.len(), two=((pl.col("buy_qty") > 0) & (pl.col("sell_qty") > 0)).sum())
        .sort("client_norm", "date")
    )
    history = per_day.rolling(index_column="date", period="365d", closed="left", group_by="client_norm").agg(
        prior_days=pl.col("n").sum(), prior_two=pl.col("two").sum()
    )
    history = history.with_columns(
        prior_two_sided=pl.when(pl.col("prior_days") > 0).then(pl.col("prior_two") / pl.col("prior_days"))
    ).select("client_norm", "date", "prior_days", "prior_two_sided")
    return clients.join(history, on=["client_norm", "date"], how="left")


def _issuer_overlap() -> pl.Expr:
    """True when the client name contains a distinctive word of the security name."""

    def words(col: str) -> pl.Expr:
        return (
            normalize_client(pl.col(col))
            .str.split(" ")
            .list.eval(pl.element().filter((pl.element().str.len_chars() >= 3) & ~pl.element().is_in(_STOP)))
        )

    return words("client_norm").list.set_intersection(words("security")).list.len() > 0


def _ends(name: pl.Expr) -> pl.Expr:
    """First and last name tokens."""
    tokens = name.str.split(" ")
    return pl.concat_list(tokens.list.first(), tokens.list.last())


def _insider_matches(clients: pl.DataFrame, insiders: pl.DataFrame) -> pl.DataFrame:
    """(client_norm, symbol) pairs that match a PIT insider of the stock: same tokens in any order, same
    first and last name in any order, or a shared rare first or last token (a family name)."""
    def keyed(frame: pl.DataFrame) -> pl.DataFrame:
        name = pl.col("client_norm")
        return frame.select(
            "client_norm",
            "symbol",
            full=name.str.split(" ").list.sort().list.join(" "),
            pair=_ends(name).list.sort().list.join(" "),
        ).unique()

    people, inside = keyed(clients), keyed(insiders)
    by_name = pl.concat(
        [people.join(inside.select("symbol", key), on=["symbol", key], how="semi") for key in ("full", "pair")]
    )
    token_counts = clients.select(pl.col("client_norm").unique()).select(tok=_ends(pl.col("client_norm"))).explode("tok", empty_as_null=False)
    rare = token_counts.group_by("tok").len().filter(
        (pl.col("len") < RARE_SURNAME) & (pl.col("tok").str.len_chars() >= 4)
    )["tok"]
    family_tokens = (
        insiders.select("symbol", tok=_ends(pl.col("client_norm"))).explode("tok", empty_as_null=False).filter(pl.col("tok").is_in(rare.implode())).unique()
    )
    by_family = people.select("client_norm", "symbol", tok=_ends(pl.col("client_norm"))).explode("tok", empty_as_null=False).join(
        family_tokens, on=["symbol", "tok"], how="semi"
    )
    return pl.concat([by_name.select("client_norm", "symbol"), by_family.select("client_norm", "symbol")]).unique()


def classify(clients: pl.DataFrame, insiders: pl.DataFrame, dictionary: pl.DataFrame) -> pl.DataFrame:
    """Add ctype (I, R, P, S), subtype, passive and directional to client-days (client_norm, symbol,
    date, security, buy_qty, sell_qty, net, gross). `insiders`: (symbol, client_norm) of PIT filers who
    are promoters, directors, KMPs or relatives of the stock."""
    matched = _insider_matches(clients, insiders).with_columns(insider=pl.lit(True))
    df = (
        churn_history(clients)
        .join(dictionary, on="client_norm", how="left")
        .join(matched, on=["client_norm", "symbol"], how="left")
        .with_columns(strategic=_issuer_overlap() | pl.col("insider").fill_null(False))
    )
    name = pl.col("client_norm")
    reference = (
        pl.when(name.str.contains(_HUF)).then(pl.lit("R:huf"))
        .when(name.str.contains(_TRUST)).then(pl.lit("R:trust"))
        .when(name.str.contains(_CORPORATE)).then(pl.lit("R:corporate"))
        .otherwise(pl.lit("R:individual"))
    )
    churner = (pl.col("prior_days") >= CHURN_MIN_DAYS) & (pl.col("prior_two_sided") >= CHURN_SHARE)
    after_strategic = _chain(
        _OTHER_INSTITUTIONS, "I", pl.when(churner.fill_null(False)).then(pl.lit("P:churner")).otherwise(reference)
    )
    code = (
        pl.when(pl.col("dict_code").is_not_null()).then(pl.col("dict_code"))
        .when(name.str.contains(_PROP)).then(pl.lit("P:prop_desk"))
        .when(name.str.contains(_SPONSOR)).then(pl.lit("S:sponsor"))
        .otherwise(_chain(_POOLED, "I", pl.when(pl.col("strategic")).then(pl.lit("S:strategic")).otherwise(after_strategic)))
    )
    return (
        df.with_columns(code=code)
        .with_columns(
            ctype=pl.col("code").str.split(":").list.first(),
            subtype=pl.col("code").str.split(":").list.last(),
            passive=name.str.contains(_PASSIVE),
            directional=pl.col("net").abs() >= 0.5 * pl.col("gross"),
        )
        .drop("code", "dict_code", "insider")
    )
