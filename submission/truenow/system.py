"""The knowledge system the harness drives: ingest snapshots, answer questions, list conflicts."""
from __future__ import annotations

import dataclasses
import sys
import traceback

from .answer import refusal, respond
from .conflicts import find_conflicts
from .extract import locator_url, schedules
from .pages import RANK
from .rates import rate_context, rate_facts
from .store import Library, Snapshot, build_page


def warm(snapshot) -> None:
    rate_facts(snapshot)
    rate_context(snapshot)
    schedules(snapshot)
    locator_url(snapshot)

_CARRIED_PENALTY = 2  # rank steps a page loses when it is only known from an earlier crawl


class TrueNow:
    def __init__(self):
        self.library = Library()
        self._answers = {}
        self._conflicts = None
        self._seen = {}  # url -> newest Page seen so far

    def ingest(self, snapshot) -> None:
        """Index one crawl. Pages missing from it but seen earlier are kept at a lower rank:
        a page that drops out of a crawl has not necessarily been removed from the site."""
        pages = [build_page(p.url, p.title, p.markdown, p.fetched_at) for p in snapshot.pages]
        present = {page.url for page in pages}
        worst = max(RANK.values())
        carried = [
            dataclasses.replace(old, rank=min(old.rank + _CARRIED_PENALTY, worst), carried=True)
            for url, old in self._seen.items() if url not in present
        ]
        indexed = Snapshot(snapshot.snapshot_id, snapshot.taken_on, pages + carried)
        warm(indexed)  # build per-snapshot structures now, so answering stays fast
        self.library.add(indexed)
        for page in pages:
            self._seen[page.url] = page
        self._answers.clear()
        self._conflicts = None

    def answer(self, question: str, as_of: str | None = None):
        key = (question.strip(), as_of)
        if key not in self._answers:
            try:
                self._answers[key] = respond(self.library, question, as_of)
            except Exception:  # never let one question take the run down
                traceback.print_exc(file=sys.stderr)
                self._answers[key] = refusal()
        return self._answers[key]

    def conflicts(self) -> list:
        if self._conflicts is None:
            self._conflicts = find_conflicts(self.library)
        return self._conflicts


def build() -> TrueNow:
    return TrueNow()
