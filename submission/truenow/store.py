"""Snapshots as the system keeps them: cleaned pages, table rows and searchable passages."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .clean import clean_markdown, plain, strip_teasers
from .pages import RANK, page_type, published_date
from .search import Index
from .tables import parse_tables

_TITLE_SUFFIX = re.compile(r"\s*\|\s*Dupaco\s*$")


@dataclass
class Page:
    url: str
    title: str
    kind: str
    rank: int
    fetched: str    # ISO date this copy of the page was crawled
    published: str  # ISO date shown on news releases and articles, or ""
    text: str       # cleaned markdown
    rows: list = field(default_factory=list)


@dataclass
class Passage:
    url: str
    heading: str
    text: str       # markdown of the paragraph (links kept)
    line: int


def build_page(url: str, title: str, markdown: str, fetched_at: str) -> Page:
    text = strip_teasers(clean_markdown(markdown))
    kind = page_type(url)
    return Page(
        url=url,
        title=_TITLE_SUFFIX.sub("", title or ""),
        kind=kind,
        rank=RANK[kind],
        fetched=(fetched_at or "")[:10],
        published=published_date(url, text),
        text=text,
        rows=parse_tables(text),
    )


def split_passages(page: Page) -> list:
    """Paragraphs under their nearest heading; table rows become one passage each."""
    out, block = [], []
    heading, start = page.title, 0

    def flush():
        if block:
            out.append(Passage(page.url, heading, " ".join(block), start))
            block.clear()

    for number, line in enumerate(page.text.splitlines()):
        stripped = line.strip()
        if not stripped or stripped.startswith("|"):
            flush()
            continue
        if stripped.startswith("#"):
            flush()
            heading = plain(stripped.lstrip("#").strip()) or heading
            continue
        if stripped.startswith(("![", "[![")):  # images and image links carry no text
            flush()
            continue
        if not block:
            start = number
        block.append(stripped)
    flush()
    for row in page.rows:
        cells = " | ".join(c for c in row.cells if c)
        out.append(Passage(page.url, row.label or heading, cells, row.line))
    return out


class Snapshot:
    """One crawl of the site, indexed for search."""

    def __init__(self, snapshot_id: str, taken_on: str, pages: list):
        self.snapshot_id = snapshot_id
        self.taken_on = taken_on
        self.pages = {page.url: page for page in pages}
        self.passages = [p for page in pages for p in split_passages(page)]
        self.by_url = {}
        for position, passage in enumerate(self.passages):
            self.by_url.setdefault(passage.url, []).append(position)
        self.index = Index([f"{p.heading} {plain(p.text)}" for p in self.passages])
        hosts = {}
        for page in pages:
            host = page.url.split("/")[2] if "://" in page.url else ""
            hosts[host] = hosts.get(host, 0) + 1
        main = max(hosts, key=hosts.get) if hosts else ""
        self.domain = ".".join(main.split(".")[-2:])  # "dupaco.com"
        self.cache = {}  # per-snapshot derived structures (rate facts and so on)


class Library:
    """All snapshots, oldest first."""

    def __init__(self):
        self.snapshots = []

    def add(self, snapshot: Snapshot) -> None:
        self.snapshots.append(snapshot)
        self.snapshots.sort(key=lambda s: s.taken_on)

    @property
    def latest(self) -> Snapshot:
        return self.snapshots[-1]

    def at(self, day: str | None) -> Snapshot:
        """The newest snapshot taken on or before `day` (ISO); the latest when no day is given."""
        if not day:
            return self.latest
        earlier = [s for s in self.snapshots if s.taken_on <= day]
        return earlier[-1] if earlier else self.snapshots[0]

    def before_first(self, day: str | None) -> bool:
        return bool(day) and day < self.snapshots[0].taken_on
