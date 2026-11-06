"""Page types, and how far each type is trusted when pages disagree.

The type comes from the URL alone, so it is stable across snapshots. Site
structure that this depends on (which sections hold product pages, which pages
are rate sheets) is kept in this module only.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

# Lower rank = more authoritative for rates, fees and product terms.
RANK = {
    "rate_sheet": 0,  # the official rate pages
    "product": 1,     # product, service, contact and about pages
    "document": 2,    # PDFs and spreadsheets (fee schedules, disclosures, reports)
    "campaign": 3,    # top-level landing and promotion pages
    "calculator": 4,  # calculators keep old default rates
    "news": 4,        # dated press releases
    "article": 5,     # blog and resource articles
}

_RATE_SHEET = re.compile(r"^/(?:[a-z]+-)?rates$")
_SECTIONS = frozenset({
    "personal", "business", "about", "savings-account", "checking-account",
    "connect", "online-and-mobile-banking",
})
_CORE_SINGLE_PAGES = frozenset({
    "contact", "membership", "mobile", "shine", "protection", "financial-services",
})
_DATED_POST = re.compile(r"^/(\d{4})/(\d{2})/(\d{2})/")
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_DATE_LINE = re.compile(r"^([A-Za-z]{3,9})\.? (\d{1,2}), (\d{4})$")


def month_number(name: str) -> int:
    return _MONTHS.get(name[:3].lower(), 0)


def published_date(url: str, text: str) -> str:
    """ISO date a news release or article was published, or "" when the page shows none."""
    dated = _DATED_POST.match(url_path(url) + "/")
    if dated:
        return "-".join(dated.groups())
    for line in text.splitlines()[:25]:
        found = _DATE_LINE.match(line.strip())
        if found and month_number(found.group(1)):
            month, day, year = month_number(found.group(1)), int(found.group(2)), int(found.group(3))
            if 1 <= day <= 31:
                return f"{year:04d}-{month:02d}-{day:02d}"
    return ""


def url_path(url: str) -> str:
    return urlparse(url).path.lower().rstrip("/") or "/"


def page_type(url: str) -> str:
    path = url_path(url)
    parts = [p for p in path.split("/") if p]
    first = parts[0] if parts else ""
    if path == "/":
        return "product"
    if _RATE_SHEET.match(path):
        return "rate_sheet"
    if first == "hubfs" or path.endswith((".pdf", ".xls", ".xlsx", ".doc", ".docx")):
        return "document"
    if "calculator" in path:
        return "calculator"
    if first in ("newsroom", "news", "press"):
        return "news"
    if first in ("learn", "blog", "resources") or _DATED_POST.match(path):
        return "article"
    if first == "business" and len(parts) > 1 and parts[1] == "resources":
        return "article"
    if first in _SECTIONS:
        return "product"
    if len(parts) == 1:
        return "product" if first in _CORE_SINGLE_PAGES else "campaign"
    return "campaign"


def rank(url: str) -> int:
    return RANK[page_type(url)]
