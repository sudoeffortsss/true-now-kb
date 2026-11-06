"""Places where the site contradicts itself, with the value the system trusts.

Everything is read from the newest snapshot: stale pages and old releases stay
on the site, so the contradictions are all visible there.

1. Rate rows that disagree with the rate sheet. A row on another page is tied to
   the rate-sheet product it describes (that product must be named near the row:
   in the labels above its table or in the page title) and to the rate-sheet row
   with the same term, balance tier or model years. It is a conflict only if no
   equally good rate-sheet row shows the same value, so different tiers of one
   product never count. One entry per product and term, all values listed.
2. Organisation facts that dated releases state differently: branch counts and
   "the Nth time <organisation> recognized" statements. The newest release wins.
"""
from __future__ import annotations

import re
from collections import OrderedDict

from .clean import plain
from .contract_types import Conflict, Source
from .rates import norm, rate_facts
from .text import tokens

_GENERIC = frozenset(tokens("loan loans rate rates account accounts apy apr dividend interest", keep_stopwords=True))
_ORDINALS = ("first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth").split()
_BRANCH_COUNT = re.compile(
    r"(?<![\d,.])\b(\d{1,3})\s+(?:full-service\s+)?(branch office locations|branch offices|branch locations|branches)\b",
    re.I)
_ORDINAL_TIME = re.compile(
    r"\b(" + "|".join(_ORDINALS) + r")\s+(?:consecutive\s+)?time\b(?:\s+(?:that|by))?\s+([A-Z][\w&.'-]+)")
_NOT_ORGS = {"Dupaco", "The", "This", "That", "It", "We", "Our"}


def find_conflicts(library) -> list:
    snapshot = library.latest
    return _rate_conflicts(snapshot) + _count_conflicts(snapshot) + _ordinal_conflicts(snapshot)


# --- rates -------------------------------------------------------------------------------

def _context_words(snapshot, fact) -> frozenset:
    page = snapshot.pages[fact.url]
    lines = page.text.splitlines()
    above = plain(" ".join(lines[max(0, fact.line - 10):fact.line]))
    return frozenset(tokens(f"{page.title} {fact.label} {fact.name} {above}"))


def _product_words(fact) -> frozenset:
    return frozenset(w for w in fact.label_words if w not in _GENERIC and not w.isdigit())


def _close(a: float, b: float) -> bool:
    return abs(min(a, 1e12) - min(b, 1e12)) <= 1.0


def _row_fit(ref, other):
    """How well two rows describe the same tier (None when they cannot)."""
    if not ({k for k, _, _ in ref.values} & {k for k, _, _ in other.values}):
        return None
    fit, keyed = 0.0, False
    if ref.term is not None or other.term is not None:
        if ref.term != other.term:
            return None
        fit, keyed = fit + 1.0, True
    if ref.amount or other.amount:
        if not (ref.amount and other.amount and _close(ref.amount[0], other.amount[0])
                and _close(ref.amount[1], other.amount[1])):
            return None
        fit, keyed = fit + 1.0, True
    if ref.model or other.model:
        if not (ref.model and other.model):
            return None
        low, high = max(ref.model[0], other.model[0]), min(ref.model[1], other.model[1])
        if low > high:
            return None
        span = max(ref.model[1], other.model[1]) - min(ref.model[0], other.model[0]) + 1
        fit, keyed = fit + (high - low + 1) / span, True
    ref_names = {w for w in ref.name_words if not w.isdigit()}
    other_names = {w for w in other.name_words if not w.isdigit()}
    if ref_names or other_names:
        share = len(ref_names & other_names) / len(ref_names | other_names)
        if not keyed and share < 0.5:
            return None
        fit += share
    return fit


def _same_values(ref, other) -> bool:
    shared = {k for k, _, _ in ref.values} & {k for k, _, _ in other.values}
    return all(ref.number(k) == other.number(k) for k in shared)


def _rate_conflicts(snapshot) -> list:
    facts = rate_facts(snapshot)
    today = snapshot.taken_on
    refs = [f for f in facts if f.rank == 0 and not f.carried and not (f.expires and f.expires < today)]
    merged = OrderedDict()
    for other in facts:
        if other.rank == 0 or other.carried:
            continue
        context = _context_words(snapshot, other)
        candidates = []
        for ref in refs:
            words = _product_words(ref)
            if not words:
                continue
            named = len(words & context) / len(words)
            if named < 0.5:
                continue
            foreign = {w for w in other.name_words if not w.isdigit() and w not in _GENERIC} - ref.words
            if foreign:
                continue  # the row names a different product ("Achieve Money Market")
            fit = _row_fit(ref, other)
            if fit is not None:
                candidates.append((named, fit, ref))
        if not candidates:
            continue
        best = max((named, fit) for named, fit, _ in candidates)
        tied = [ref for named, fit, ref in candidates if (named, fit) == best]
        if any(_same_values(ref, other) for ref in tied):
            continue
        ref = tied[0]
        key = (norm(ref.label), ref.term)
        entry = merged.setdefault(key, {"ref": ref, "old": [], "new": [], "urls": [], "ended": ""})
        for kind, _, value in other.values:
            current = ref.cell(kind)
            if current and other.number(kind) != ref.number(kind):
                if value not in entry["old"]:
                    entry["old"].append(value)
                if current not in entry["new"]:
                    entry["new"].append(current)
        if other.url not in entry["urls"]:
            entry["urls"].append(other.url)
        if other.expires and other.expires < today:
            entry["ended"] = other.expires
    out = []
    for entry in merged.values():
        if not entry["old"]:
            continue
        ref = entry["ref"]
        present = {k for k, _, _ in ref.values}
        kinds = [k for k in ("rate", "apr", "dividend", "apy") if k in present]
        metric = " and ".join(k.upper() if k in ("apy", "apr") else k for k in kinds)
        term = _term_text(ref.term)
        topic = " ".join(p for p in (ref.label, term, metric) if p)
        pages = ", ".join(entry["urls"][:4])
        reason = (f"The rate sheet ({ref.url}) is the official source; {pages} still show older figures.")
        if entry["ended"]:
            reason = f"The offer on {pages} ended on {entry['ended']}; the rate sheet shows the current rate."
        sources = [Source(url=ref.url, snapshot_id=snapshot.snapshot_id, quote=" / ".join(entry["new"]))]
        sources += [Source(url=u, snapshot_id=snapshot.snapshot_id) for u in entry["urls"]]
        out.append(Conflict(topic=topic, values=entry["old"] + entry["new"], sources=sources,
                            resolution=" / ".join(entry["new"]), reason=reason))
    return out


def _term_text(months) -> str:
    if not months:
        return ""
    if months >= 120 and months % 12 == 0:
        return f"{months // 12}-year"
    return f"{months}-month"


# --- organisation facts in dated releases -----------------------------------------------

def _dated_sentences(snapshot, pattern):
    for page in snapshot.pages.values():
        if page.carried:
            continue
        for line in page.text.splitlines():
            for sentence in re.split(r"(?<=[.!?])\s+", plain(line)):
                found = pattern.search(sentence)
                if found:
                    yield found, sentence, page


def _count_conflicts(snapshot) -> list:
    stated = {}
    for found, sentence, page in _dated_sentences(snapshot, _BRANCH_COUNT):
        number = int(found.group(1))
        known = stated.get(number)
        if known is None or (page.published or "") > (known[1].published or ""):
            stated[number] = (found.group(0), page)
    if len(stated) < 2:
        return []
    ordered = sorted(stated.items(), key=lambda item: item[1][1].published or "0000")
    newest_number, (newest_phrase, newest_page) = ordered[-1]
    if not newest_page.published:
        return []
    values = [phrase for _, (phrase, _) in ordered]
    sources = [Source(url=page.url, snapshot_id=snapshot.snapshot_id, quote=phrase) for _, (phrase, page) in ordered]
    return [Conflict(
        topic="Number of Dupaco branch office locations",
        values=values, sources=sources, resolution=newest_phrase,
        reason=f"Releases give different counts; the newest ({newest_page.published}) says {newest_phrase}.",
    )]


def _ordinal_conflicts(snapshot) -> list:
    by_org = {}
    for found, sentence, page in _dated_sentences(snapshot, _ORDINAL_TIME):
        org = found.group(2).rstrip(".")
        if org in _NOT_ORGS:
            continue
        ordinal = found.group(1).lower()
        by_org.setdefault(org, {})
        known = by_org[org].get(ordinal)
        if known is None or (page.published or "") > (known.published or ""):
            by_org[org][ordinal] = page
    out = []
    for org, ordinals in by_org.items():
        if len(ordinals) < 2:
            continue
        ordered = sorted(ordinals.items(), key=lambda item: (item[1].published or "0000", _ORDINALS.index(item[0])))
        newest, page = ordered[-1]
        out.append(Conflict(
            topic=f"How many times {org} has recognized Dupaco",
            values=[o for o, _ in ordered],
            sources=[Source(url=p.url, snapshot_id=snapshot.snapshot_id, quote=o) for o, p in ordered],
            resolution=newest,
            reason=f"Older releases give an earlier count; the newest ({page.published}) says the {newest} time.",
        ))
    return out
