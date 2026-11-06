"""Markdown table parsing.

Rate pages show each product as a short label line followed by a table and a
line such as "Rates are effective as of 09/01/2026". Two label lines in a row
mean "section, then product" (for example "Certificates", "Special
Certificates"). Each table row becomes a TableRow that keeps those labels, the
header and the effective date.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .text import expiry_date

_SEPARATOR = re.compile(r"^\s*\|(\s*:?-{3,}:?\s*\|)+\s*$")
_EFFECTIVE = re.compile(r"effective (?:as of )?(\d{1,2}/\d{1,2}/\d{4})", re.I)
_LINK_ONLY = re.compile(r"^(\[[^\]]*\]\([^)]*\)\s*)+$")
_MAX_LABEL = 60


@dataclass(frozen=True)
class TableRow:
    section: str
    label: str
    header: tuple
    cells: tuple
    effective: str  # "MM/DD/YYYY" found just below the table, or ""
    line: int       # 0-based line of the row in the cleaned page text
    expires: str = ""  # ISO date an offer note near the table says it ends, or ""

    def cell(self, column: str) -> str:
        """Value of the first column whose header contains `column` (case-insensitive)."""
        key = column.lower()
        for name, value in zip(self.header, self.cells):
            if key in name.lower():
                return value
        return ""


_HEADER_NOTE = re.compile(r"(?<=[A-Za-z)])\s*\d$")
_CELL_NOTE = re.compile(r"(%|APR|APY)\s+\d$")


def split_row(line: str) -> tuple:
    return tuple(_CELL_NOTE.sub(r"\1", part.strip()) for part in line.strip().strip("|").split("|"))


def split_header(line: str) -> tuple:
    """Header cells without footnote markers ("(As Low As) 1", "(APR)*")."""
    return tuple(_HEADER_NOTE.sub("", cell).rstrip("* ").strip() for cell in split_row(line))


def _label_text(line: str) -> str:
    s = line.strip()
    if not s or s.startswith("|") or "](" in s or _LINK_ONLY.match(s):
        return ""
    if s.startswith("#"):
        return s.lstrip("#").strip()
    if len(s) > _MAX_LABEL or s.endswith((".", ":", "!", "?", ",")):
        return ""
    return s


def parse_tables(text: str) -> list:
    """All table rows in a cleaned page, in page order."""
    lines = text.splitlines()
    rows = []
    section = label = ""
    last_was_label = False
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith("|") and i + 1 < len(lines) and _SEPARATOR.match(lines[i + 1]):
            header = split_header(lines[i])
            j = i + 2
            body = []
            while j < len(lines) and lines[j].strip().startswith("|"):
                body.append((j, split_row(lines[j])))
                j += 1
            effective = expires = ""
            for k in range(j, min(j + 8, len(lines))):
                if lines[k].strip().startswith("|"):
                    break
                found = _EFFECTIVE.search(lines[k])
                if found and not effective:
                    effective = found.group(1)
                expires = expires or expiry_date(lines[k])
            expires = expires or expiry_date(f"{section} {label}")
            rows.extend(TableRow(section, label, header, cells, effective, n, expires) for n, cells in body)
            last_was_label = False
            i = j
            continue
        text_label = _label_text(lines[i])
        if text_label:
            if last_was_label:
                section, label = label, text_label
            else:
                label = text_label
            last_was_label = True
        elif stripped:
            last_was_label = False
        i += 1
    return rows
