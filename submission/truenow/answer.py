"""Turns a question into an Answer: pick the snapshot for the date, then the reader that fits."""
from __future__ import annotations

import re

from .contract_types import Answer, Source
from .extract import (find_address, find_count, find_event, find_hours, find_link, find_passage,
                      find_phone)
from .query import analyze
from .rates import VARIANTS, find_rates, format_rates
from .text import tokens, words_to_digits

REFUSAL = ("I can't confirm that from our website: it isn't listed there. "
           "Please contact us directly for this detail.")
STATUS_UNLISTED = ("That isn't listed on the current rate sheet, so I can't confirm it is still offered. "
                   "Please contact us directly.")
_PHONE_QUESTION = re.compile(r"\b(phone|telephone)\b|\bcall\b.*\bnumber\b|\bnumber\b.*\bcall\b", re.I)


def _states_rate(query, text: str) -> bool:
    """A passage only answers a rate question if it gives a rate for the term and variant asked."""
    lower = words_to_digits(text.lower())
    if not re.search(r"\d+(?:\.\d+)?\s*%", lower):
        return False
    for months in query.terms:
        said = re.search(rf"\b{months}[\s-]*(?:month|mo)", lower)
        if not said and months % 12 == 0:
            said = re.search(rf"\b{months // 12}[\s-]*(?:year|yr)", lower)
        if not said:
            return False
    asked = set(query.words) & VARIANTS
    return asked <= set(tokens(text))
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
    return respond_traced(library, question, as_of)[0]


def respond_traced(library, question: str, as_of: str | None = None) -> tuple:
    """(Answer, name of the reader that produced it). The model layer routes on the name."""
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
                    return _answer(f"You can see them at {listed}.", [(u, "") for u in pages[:2]], snapshot), "rates_link"
        found = find_link(query, snapshot)
        if found:
            return _answer(found.text, found.sources, snapshot), "link"

    if _PHONE_QUESTION.search(question) and not re.search(r"\brouting\b|\baccount number\b", question, re.I):
        found = find_phone(snapshot)
        if found:
            return _answer(found.text, found.sources, snapshot), "phone"

    if query.asks_status and query.names_product:
        # "Is the 25-month certificate special still offered?": the current rate sheet decides.
        rates = find_rates(query, snapshot)
        if rates is not None:
            if rates.declined or not rates.facts:
                return Answer(text=STATUS_UNLISTED, sources=[], confident=False), "status_unlisted"
            sources = [(u, "") for u in rates.sources]
            return _answer("Yes. " + format_rates(query, rates), sources, snapshot), "rates_status"

    if query.is_rate:
        rates = find_rates(query, snapshot)
        if rates is not None:
            if rates.declined:
                return refusal(), "refusal_rates_declined"
            note = None
            if rates.older:
                pages = sorted({f.url for f in rates.older})[:3]
                note = "Older figures still appear on " + ", ".join(pages) + "; the rate sheet value is used."
            sources = [(u, "") for u in rates.sources]
            return _answer(format_rates(query, rates), sources, snapshot, note), "rates"

    if query.asks_hours:
        found = find_hours(query, snapshot)
        if found is not None:
            if not found.confident:
                return refusal(), "refusal_hours"
            return _answer(found.text, found.sources, snapshot), "hours"

    if query.asks_address:
        found = find_address(query, snapshot)
        if found:
            return _answer(found.text, found.sources, snapshot), "address"

    if query.asks_count:
        found = find_count(query, snapshot, today)
        if found:
            return _answer(found.text, found.sources, snapshot), "count"

    if query.asks_when or query.asks_status:
        found = find_event(query, snapshot, today)
        if found:
            return _answer(found.text, found.sources, snapshot), "event"

    found = find_passage(query, snapshot, prefer_new=bool(_NEWS_TOPICS.search(question)))
    if found and query.is_rate and not _states_rate(query, found.text):
        return refusal(), "refusal_rate_not_stated"  # the site talks about the product but publishes no such rate
    if found:
        return _answer(found.text, found.sources, snapshot), "passage"
    return refusal(), "refusal_nothing_found"
