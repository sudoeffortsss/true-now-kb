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


def term_months(text: str) -> list:
    """Loan or deposit terms mentioned in text, in months ("60-month" -> 60, "30-year" -> 360)."""
    out = []
    for number, unit in _TERM.findall(text):
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
