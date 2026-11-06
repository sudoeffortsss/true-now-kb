"""Tokens, synonyms and small parsers shared by search and question analysis."""
from __future__ import annotations

import re

_WORD = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset("""
a about all also am an and any are as at be been but by can could did do does for from get give
had has have how i if in into is it its just know let like me my need of on or our please
provide should so some tell than that the their them then there these they this to us want was
we were what when where which who whom why will with would you your dupaco dupaco's
""".split())

# Canonical token for words the site and its members use interchangeably.
_CANON = {
    "cd": "certificate", "cds": "certificate", "certificat": "certificate",
    "car": "auto", "vehicle": "auto", "automobile": "auto", "truck": "auto",
    "house": "home", "visa": "card", "mastercard": "card",
    "yield": "apy", "apys": "apy",
    "aprs": "apr",
    "saving": "savings",
    "mo": "month", "months": "month", "monthly": "month",
    "yr": "year", "years": "year", "yearly": "year",
    "drive": "driveup", "driveup": "driveup", "drivethru": "driveup", "thru": "driveup",
    "phone": "call", "telephone": "call", "number": "number",
    "located": "location", "locations": "location", "address": "location",
    "open": "open", "opening": "open", "opened": "open",
    "close": "close", "closed": "close", "closing": "close", "closure": "close",
    "join": "join", "joining": "join", "membership": "member", "members": "member",
    "flexibility": "flexible", "eligibility": "eligible", "availability": "available",
    "vto": "volunteer", "recognition": "recognize", "recognitions": "recognize",
}


# Verbs whose -ing / -ed / -s forms should match the base form ("updating" ~ "update").
_VERBS = frozenset("""
update change close open join apply earn pay add use find move transfer deposit withdraw call visit
order replace report lose lock reset enroll register sign log access set schedule receive qualify
refinance finance borrow save locate recognize rank name award offer charge
""".split())


def _verb(token: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            base = token[: -len(suffix)]
            for candidate in (base, base + "e", base[:-1] if base[-1:] == base[-2:-1] else ""):
                if candidate in _VERBS:
                    return candidate
    return ""


def stem(token: str) -> str:
    if token in _VERBS:
        return token
    verb = _verb(token)
    if verb:
        return verb
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 5 and token.endswith(("ches", "shes", "xes", "sses", "zes")):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def canon(token: str) -> str:
    token = _CANON.get(token, token)
    token = stem(token)
    return _CANON.get(token, token)


def tokens(text: str, keep_stopwords: bool = False) -> list:
    """Lowercased canonical word tokens, without stopwords unless asked."""
    text = text.lower().replace("’", "'").replace("'s", "")
    text = text.replace("drive-up", "driveup").replace("drive-thru", "driveup")
    text = re.sub(r"\bhome[\s-]+loans?\b", "mortgage", text)
    out = []
    for word in _WORD.findall(text):
        if not keep_stopwords and word in STOPWORDS:
            continue
        out.append(canon(word))
    return out


# --- numbers in questions and table cells -------------------------------------------

_MONEY = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*(k|thousand|million|m)?\b", re.I)
_TERM = re.compile(r"\b(\d{1,3})[\s-]*(month|mo|year|yr)s?\b", re.I)
_MODEL_RANGE = re.compile(r"\b((?:19|20)\d{2})\s*[-\u2013]\s*(\d{2,4})\b")
_YEAR = re.compile(r"\b((?:19|20)\d{2})\b")


def money_values(text: str) -> list:
    out = []
    for amount, scale in _MONEY.findall(text):
        value = float(amount.replace(",", ""))
        if scale.lower() in ("k", "thousand"):
            value *= 1_000
        elif scale.lower() in ("m", "million"):
            value *= 1_000_000
        out.append(value)
    return out


_UNITS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
         "ninety": 90}
_NUMBER_WORD = re.compile(
    r"\b(?:(" + "|".join(_TENS) + r")(?:[\s-](" + "|".join(list(_UNITS)[:9]) + r"))?|(" + "|".join(_UNITS) + r"))\b",
    re.I)


def words_to_digits(text: str) -> str:
    """"six month" -> "6 month", "thirty-six months" -> "36 months"."""
    def repl(match):
        tens, unit, alone = match.group(1), match.group(2), match.group(3)
        if alone:
            return str(_UNITS[alone.lower()])
        return str(_TENS[tens.lower()] + (_UNITS[unit.lower()] if unit else 0))
    return _NUMBER_WORD.sub(repl, text)


def term_months(text: str) -> list:
    """Loan or deposit terms mentioned in text, in months ("60-month" -> 60, "30-year" -> 360)."""
    out = []
    for number, unit in _TERM.findall(words_to_digits(text)):
        n = int(number)
        out.append(n * 12 if unit.lower().startswith("y") else n)
    return out


def amount_range(cell: str):
    """(low, high) for cells like "$10,000 - $24,999.99", "$25,000+", "$832,750 or less"."""
    values = money_values(cell.replace("\u2013", "-"))
    if not values:
        bare = re.findall(r"\d[\d,]*(?:\.\d+)?", cell)
        if "$" not in cell or not bare:
            return None
        values = [float(b.replace(",", "")) for b in bare]
    low = values[0]
    lower_cell = cell.lower()
    if "or less" in lower_cell or "up to" in lower_cell:
        return (0.0, low)
    if cell.strip().endswith("+") or "or more" in lower_cell:
        return (low, float("inf"))
    if len(values) >= 2:
        return (min(values[0], values[1]), max(values[0], values[1]))
    nums = re.findall(r"\d[\d,]*", cell)
    if len(nums) >= 2:  # "$10,000-24,999"
        return (low, float(nums[1].replace(",", "")))
    return (low, low)


_MONTH_NUMBERS = {name: i for i, name in enumerate(
    ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
     "november", "december"), 1)}
_DATE = (r"(?:(20\d{2})-(\d{2})-(\d{2})|(\d{1,2})/(\d{1,2})/(20\d{2})|"
         r"(" + "|".join(_MONTH_NUMBERS) + r")\s+(\d{1,2}),?\s+(20\d{2}))")
_EXPIRY = re.compile(r"\b(?:ended|ends|expired|expires|expiring|valid through|valid until|available through|"
                     r"good through|offered through|through|until)\s+(?:on\s+)?" + _DATE, re.I)


def expiry_date(text: str) -> str:
    """ISO date an offer is said to end ("offer ended 2025-09-30", "valid through June 30, 2026"), or ""."""
    found = _EXPIRY.search(text)
    if not found:
        return ""
    g = found.groups()
    if g[0]:
        return f"{g[0]}-{g[1]}-{g[2]}"
    if g[3]:
        return f"{int(g[5]):04d}-{int(g[3]):02d}-{int(g[4]):02d}"
    return f"{int(g[8]):04d}-{_MONTH_NUMBERS[g[6].lower()]:02d}-{int(g[7]):02d}"


def model_year_range(cell: str):
    """(first, last) model year for "2024-27 Model Year", "Model Year 2026-23", "2016 (and older)"."""
    if "model year" not in cell.lower():
        return None
    found = _MODEL_RANGE.search(cell)
    if found:
        first = int(found.group(1))
        tail = found.group(2)
        second = int(tail) if len(tail) == 4 else (first // 100) * 100 + int(tail)
        return (min(first, second), max(first, second))
    year = _YEAR.search(cell)
    if not year:
        return None
    y = int(year.group(1))
    if "older" in cell.lower() or "before" in cell.lower():
        return (0, y)
    return (y, y)
