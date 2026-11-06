"""The knowledge system the harness drives: ingest snapshots, answer questions, list conflicts."""
from __future__ import annotations

import sys
import traceback

from .answer import refusal, respond
from .conflicts import find_conflicts
from .store import Library, Snapshot, build_page


class TrueNow:
    def __init__(self):
        self.library = Library()
        self._answers = {}
        self._conflicts = None

    def ingest(self, snapshot) -> None:
        pages = [build_page(p.url, p.title, p.markdown, p.fetched_at) for p in snapshot.pages]
        self.library.add(Snapshot(snapshot.snapshot_id, snapshot.taken_on, pages))
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
