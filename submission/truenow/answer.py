"""Turns a question into an Answer: pick the snapshot for the date, then the reader that fits."""
from __future__ import annotations

import re

from .contract_types import Answer, Source
from .extract import find_address, find_count, find_event, find_hours, find_link, find_passage
from .query import analyze
from .rates import find_rates, format_rates

REFUSAL = ("I can't confirm that from Dupaco's website: it isn't listed there. "
           "Please contact Dupaco directly for this detail.")
_NEWS_TOPICS = re.compile(r"\b(award|awards|recogni\w+|ranked|ranking|named|forbes|honor\w*|anniversary|"
                          r"how long|latest|recent)\b", re.I)


def refusal() -> Answer:
    return Answer(text=REFUSAL, sources=[], confident=False)


def _answer(text: str, sources: list, snapshot, note: str | None = None) -> Answer:
    seen, cited = set(), []
    for url, quote in sources:
        if url in seen:
            continue
        seen.add(url)
        cited.append(Source(url=url, snapshot_id=snapshot.snapshot_id, quote=(quote or "")[:300] or None))
    return Answer(text=text, sources=cited, confident=True, conflict_note=note)


def respond(library, question: str, as_of: str | None = None) -> Answer:
    query = analyze(question, as_of)
    snapshot = library.at(query.day)
    today = query.day or library.latest.taken_on

    if query.wants_link:
        if query.is_rate:
            rates = find_rates(query, snapshot)
            if rates and rates.facts:
                pages = [u for u in rates.sources if snapshot.pages[u].rank == 1] + \
                        [u for u in rates.sources if snapshot.pages[u].rank == 0]
                if pages:
                    listed = " or ".join(pages[:2])
                    return _answer(f"You can see them at {listed}.", [(u, "") for u in pages[:2]], snapshot)
        found = find_link(query, snapshot)
        if found:
            return _answer(found.text, found.sources, snapshot)

    if query.is_rate:
        rates = find_rates(query, snapshot)
        if rates is not None:
            if rates.declined:
                return refusal()
            note = None
            if rates.older:
                pages = sorted({f.url for f in rates.older})[:3]
                note = "Older figures still appear on " + ", ".join(pages) + "; the rate sheet value is used."
            sources = [(u, "") for u in rates.sources]
            return _answer(format_rates(query, rates), sources, snapshot, note)

    if query.asks_hours:
        found = find_hours(query, snapshot)
        if found is not None:
            if not found.confident:
                return refusal()
            return _answer(found.text, found.sources, snapshot)

    if query.asks_address:
        found = find_address(query, snapshot)
        if found:
            return _answer(found.text, found.sources, snapshot)

    if query.asks_count:
        found = find_count(query, snapshot, today)
        if found:
            return _answer(found.text, found.sources, snapshot)

    if query.asks_when or query.asks_status:
        found = find_event(query, snapshot, today)
        if found:
            return _answer(found.text, found.sources, snapshot)

    found = find_passage(query, snapshot, prefer_new=bool(_NEWS_TOPICS.search(question)))
    if found:
        return _answer(found.text, found.sources, snapshot)
    return refusal()
