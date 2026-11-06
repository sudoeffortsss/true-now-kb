"""Places where the site contradicts itself, with the value the system trusts.

First version: rate rows on lower-ranked pages (campaign pages, calculators,
news) whose name matches a row on the rate sheet but whose values differ.
"""
from __future__ import annotations

from collections import OrderedDict

from .contract_types import Conflict, Source
from .rates import norm, rate_facts


def find_conflicts(library) -> list:
    snapshot = library.latest
    facts = rate_facts(snapshot)
    official = {}
    for fact in facts:
        if fact.rank == 0:
            official.setdefault(norm(fact.name or fact.label), fact)
    merged = OrderedDict()
    for fact in facts:
        if fact.rank < 2:
            continue
        current = official.get(norm(fact.name or fact.label))
        if not current or not (current.label_words & fact.label_words):
            continue
        for kind, _, value in fact.values:
            ref = current.cell(kind)
            if not ref or norm(ref) == norm(value):
                continue
            key = (current.group, current.name, kind)
            entry = merged.setdefault(key, {"current": current, "kind": kind, "ref": ref, "values": [], "urls": []})
            if value not in entry["values"]:
                entry["values"].append(value)
            if fact.url not in entry["urls"]:
                entry["urls"].append(fact.url)
    out = []
    for entry in merged.values():
        current = entry["current"]
        topic = f"{current.label} {current.name} {entry['kind'].upper() if entry['kind'] in ('apy', 'apr') else entry['kind']}"
        sources = [Source(url=current.url, snapshot_id=snapshot.snapshot_id, quote=entry["ref"])]
        sources += [Source(url=u, snapshot_id=snapshot.snapshot_id) for u in entry["urls"]]
        out.append(Conflict(
            topic=topic.strip(),
            values=entry["values"] + [entry["ref"]],
            sources=sources,
            resolution=entry["ref"],
            reason="The rate sheet is the official source; the other pages show an older value.",
        ))
    return out
