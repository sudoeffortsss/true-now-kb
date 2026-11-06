"""Question analysis: what is asked, about which product, for which date."""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass

from .text import money_values, term_months, tokens

_MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december"
_MONTH_NUMBER = {name: i for i, name in enumerate(_MONTHS.split("|"), 1)}
_MONTH_YEAR = re.compile(rf"\b({_MONTHS})\s+(\d{{4}})\b", re.I)
_AS_OF_DAY = re.compile(rf"\bas of\s+({_MONTHS})\s+(\d{{1,2}}),?\s+(\d{{4}})\b", re.I)
_ISO_DAY = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
_ANY_DATE = re.compile(rf"\b(?:{_MONTHS})\s+(?:\d{{1,2}},?\s+)?\d{{4}}\b|\b20\d{{2}}-\d{{2}}-\d{{2}}\b", re.I)
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent)", re.I)
_YEAR = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")
_VEHICLE = re.compile(r"\b(model|vehicle|car|auto|motorcycle|truck|boat|rv|atv|snowmobile|jet ski)\b", re.I)

_LINK = re.compile(r"\b(link|links|url|urls|web ?site|web ?page|web address|page)\b", re.I)
_ONLINE_ACTION = re.compile(
    r"\b(join|apply|open|enroll|sign up|register|start|become a member)\b.*\bonline\b|"
    r"\bonline\b.*\b(join|apply|open|enroll|sign up|register|application|become a member)\b", re.I)
_HOURS = re.compile(r"\bhours?\b|\bwhat time\b|\bopen (?:on|until|till|at|late)\b", re.I)
_COUNTER = re.compile(r"\b(lobby|lobbies|drive[- ]?(?:up|thru|through))\b", re.I)
_OPEN_CLOSE = re.compile(r"\b(open|opens|close|closes|closing|opening)\b", re.I)
_PRODUCT = re.compile(r"\b(certificates?|cds?|loans?|mortgages?|accounts?|savings|checking|cards?|visa|ira|iras|"
                      r"heloc|money market|specials?)\b", re.I)
_EARNS = re.compile(r"\b(pay|pays|paying|earn|earns|earning|charge|charges)\b", re.I)
_ADDRESS = re.compile(r"\baddress\b|\bwhere is\b|\bwhere's\b|\blocated\b|\blocation of\b", re.I)
_COUNT = re.compile(r"\bhow many\b|\bnumber of\b", re.I)
_WHEN = re.compile(r"^\s*when\b|\bwhat date\b", re.I)
_STATUS = re.compile(r"\bstill (?:open|available|offered|operating|around)\b", re.I)
_YES_NO = re.compile(r"^\s*(?:do|does|is|are|can|could|will|did|has|have)\b", re.I)
_LIST = re.compile(r"\b(all|list|every|each|options|offer|offers|types|kinds)\b", re.I)
_PLURAL = re.compile(
    r"\b(rates|cards|certificates|accounts|loans|products|apys|aprs|terms|tiers|programs|services)\b", re.I)
_MAX = re.compile(r"\b(highest|best|top|most|maximum|max|largest|biggest)\b", re.I)
_MIN = re.compile(r"\b(lowest|cheapest|least|minimum|smallest)\b", re.I)


@dataclass
class Query:
    text: str
    words: list            # canonical content tokens
    day: str | None        # ISO date the question is about, None for "now"
    metric: str            # "apy", "apr", "dividend", "rate" or ""
    is_rate: bool
    terms: list            # months
    amounts: list          # dollars
    model_year: int | None
    percents: list         # percentages written in the question
    wants_list: bool
    extreme: str           # "max", "min" or ""
    wants_link: bool
    asks_hours: bool
    asks_address: bool
    asks_count: bool
    asks_when: bool
    asks_status: bool
    yes_no: bool
    names_product: bool    # mentions a deposit or loan product


def _last_day(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"


def question_day(question: str, as_of: str | None) -> str | None:
    """The date a question is about: `as_of` when given, else a date written in the question."""
    if as_of:
        found = _ISO_DAY.search(str(as_of))
        if found:
            return "-".join(found.groups())
    found = _AS_OF_DAY.search(question)
    if found:
        month = _MONTH_NUMBER[found.group(1).lower()]
        return f"{int(found.group(3)):04d}-{month:02d}-{int(found.group(2)):02d}"
    found = _ISO_DAY.search(question)
    if found:
        return "-".join(found.groups())
    found = _MONTH_YEAR.search(question)
    if found:
        return _last_day(int(found.group(2)), _MONTH_NUMBER[found.group(1).lower()])
    return None


def analyze(question: str, as_of: str | None = None) -> Query:
    lower = question.lower()
    undated = _ANY_DATE.sub(" ", question)
    if re.search(r"\bapys?\b|annual percentage yield|\byield\b", lower):
        metric = "apy"
    elif re.search(r"\baprs?\b|annual percentage rate", lower):
        metric = "apr"
    elif "dividend" in lower:
        metric = "dividend"
    elif re.search(r"\b(rate|rates|interest)\b", lower):
        metric = "rate"
    else:
        metric = ""
    percents = [float(p) for p in _PERCENT.findall(question)]
    model_year = None
    if _VEHICLE.search(undated):
        years = _YEAR.findall(undated)
        model_year = int(years[0]) if years else None
    terms = term_months(re.sub(r"\bmodel[\s-]+years?\b", " ", undated, flags=re.I))
    amounts = money_values(undated)
    extreme = "max" if _MAX.search(lower) else ("min" if _MIN.search(lower) else "")
    names_product = bool(_PRODUCT.search(lower))
    return Query(
        text=question,
        words=tokens(undated),
        day=question_day(question, as_of),
        metric=metric,
        # "How much does Bright Savings pay?" asks for a rate without saying "rate".
        is_rate=bool(metric) or bool(percents) or (names_product and bool(_EARNS.search(lower))),
        terms=terms,
        amounts=amounts,
        model_year=model_year,
        percents=percents,
        wants_list=bool(_LIST.search(lower) or _PLURAL.search(lower)),
        extreme=extreme,
        wants_link=bool(_LINK.search(lower) or _ONLINE_ACTION.search(lower)),
        asks_hours=bool(_HOURS.search(lower) or (_COUNTER.search(lower) and _OPEN_CLOSE.search(lower))),
        asks_address=bool(_ADDRESS.search(lower)),
        asks_count=bool(_COUNT.search(lower)),
        asks_when=bool(_WHEN.search(lower)),
        asks_status=bool(_STATUS.search(lower)),
        yes_no=bool(_YES_NO.search(lower)),
        names_product=names_product,
    )
