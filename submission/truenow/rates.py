"""Rate answers read from rate tables.

1. Find the products a question names, using the labels above each table and the
   row names inside it.
2. Keep rows that fit the stated term, amount and model year.
3. Drop product variants the question did not ask for (jumbo, IRA, refinance,
   introductory or promotional rates, health savings), and business accounts
   unless the question asks for everything.
4. Take values from the most authoritative page type that has them, and cite the
   other official pages that show the same row.

A product word that no rate table covers, but that the site does write about
(for example "adjustable" or "construction"), means the site lists no rate for
that product, so the question is declined instead of answered with a neighbour.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from .clean import plain
from .text import VERBS, amount_range, expiry_date, model_year_range, term_months, tokens

_PERCENT_CELL = re.compile(r"\d+(?:\.\d+)?\s*%")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_SKIP_COLUMN = re.compile(r"\bmin\b|min\.|minimum|payment|points?\b", re.I)
_EMPTY = frozenset({"", "-", "\u2014", "\u2013", "none", "n/a"})

_NOISE = frozenset(tokens(
    "rate rates apy apr dividend interest annual percentage yield current currently today now right "
    "offer offered earn earning pay paying charge much highest lowest best top tier tiers account accounts "
    "month months year years model fixed standard balance between new used option options "
    "type types price cost percent like know tell would link links url website site page see view "
    "find use check look online show finance financing borrow buy still available", keep_stopwords=True))
# Words that mark a heading as being about a product, used to tell product names from other words.
_PRODUCT_TYPES = frozenset(tokens(
    "loan loans mortgage mortgages certificate certificates account accounts card cards savings checking "
    "ira heloc deposit deposits rate rates", keep_stopwords=True))
VARIANTS = frozenset(tokens(
    "jumbo roth traditional ira refinance refi cash intro introductory transfer promotional promo "
    "equity achieve health hsa", keep_stopwords=True))
_BUSINESS = "business"
_LIST_SHARE = 0.75
_MAX_ROWS = 18
_LABELS = {
    "apy": "APY", "dividend": "dividend rate", "apr": "APR", "rate": "rate",
    "intro_apy": "APY for the first 6 months", "intro_dividend": "dividend rate for the first 6 months",
}
_SHOW_ORDER = ("rate", "apr", "dividend", "apy", "intro_dividend", "intro_apy")


def metric_kind(header: str) -> str:
    h = header.lower()
    promo = "1st 6 months" in h or "first 6 months" in h
    if "yield" in h or "apy" in h:
        return "intro_apy" if promo else "apy"
    if "dividend" in h:
        return "intro_dividend" if promo else "dividend"
    if "annual percentage rate" in h or re.search(r"\bapr\b", h):
        return "apr"
    if re.search(r"\brate\b", h):
        return "rate"
    return ""


def norm(text: str) -> str:
    text = text.replace("\u2013", "-").replace("\u2014", "-").replace("\u2019", "'")
    return re.sub(r"\s+", " ", text).strip().lower()


# "6-month certificate APY: 3.50%" or "Summer special: 6-month certificate APY 2.95% (offer ended ...)"
_TEXT_RATE = re.compile(
    r"^(?P<label>[^:|]{3,80}):\s*(?P<name>[^%:]{0,60}?)\s*(?P<value>\d+(?:\.\d+)?\s*%)\s*(?P<tail>.{0,80})$")
_METRIC_WORD = re.compile(r"\b(apy|apr|rate|dividend|yield)\b", re.I)


def _text_metric(words: str) -> str:
    for word in _METRIC_WORD.findall(words.lower()):
        return {"apy": "apy", "yield": "apy", "apr": "apr", "dividend": "dividend"}.get(word, "rate")
    return ""


@dataclass(frozen=True)
class RateFact:
    url: str
    kind: str
    rank: int
    order: int
    group: tuple   # (url, section, label)
    label: str
    name: str
    label_words: frozenset
    name_words: frozenset
    values: tuple  # ((metric kind, header, cell), ...) percentage cells only
    effective: str
    term: int | None
    amount: tuple | None
    model: tuple | None
    expires: str = ""  # ISO date the offer ended or ends, when the page says so
    line: int = 0      # line of the row in the cleaned page text
    carried: bool = False  # the page is only known from an earlier crawl

    @property
    def words(self) -> frozenset:
        return self.label_words | self.name_words

    def cell(self, kind: str) -> str:
        for k, _, value in self.values:
            if k == kind:
                return value
        return ""

    def number(self, kind: str):
        found = _NUMBER.search(self.cell(kind))
        return float(found.group(0)) if found else None

    def key(self) -> tuple:
        return (norm(self.name or self.label), tuple(norm(v) for _, _, v in self.values))


def rate_facts(snapshot) -> list:
    cached = snapshot.cache.get("rate_facts")
    if cached is not None:
        return cached
    facts, order = [], 0
    for page in snapshot.pages.values():
        for row in page.rows:
            values, names = [], []
            amount = term = model = None
            for header, cell in zip(row.header, row.cells):
                kind = metric_kind(header)
                if kind and _PERCENT_CELL.search(cell):
                    # A cell that names its own measure wins over a mislabeled header
                    # (a calculator lists "5.34% APR" under "Annual Percentage Yield").
                    if re.search(r"%\s*APR\b", cell):
                        kind = "apr"
                    elif re.search(r"%\s*APY\b", cell):
                        kind = "apy"
                    values.append((kind, header, cell))
                    continue
                if kind or _SKIP_COLUMN.search(header) or norm(cell) in _EMPTY:
                    continue
                if norm(cell) == norm(row.label):
                    continue
                names.append(cell)
                if amount is None and "$" in cell:
                    amount = amount_range(cell)
                if term is None:
                    found = term_months(cell)
                    term = found[0] if found else None
                if model is None:
                    model = model_year_range(cell)
            if not values:
                continue
            name = ", ".join(plain(n) for n in names)
            facts.append(RateFact(
                url=page.url, kind=page.kind, rank=page.rank, order=order,
                group=(page.url, row.section, row.label), label=plain(row.label), name=name,
                # Labels, plus the names of descriptive columns ("Certificate", "Card Type", "IRA Type"),
                # which say what product the table lists even when its label is just "Regular".
                # A business page's rows belong to business accounts even when the label omits it.
                label_words=_label_words(page, row, term, values),
                name_words=frozenset(tokens(name)),
                values=tuple(values), effective=row.effective,
                term=term, amount=amount, model=model, expires=row.expires,
                line=row.line, carried=page.carried,
            ))
            order += 1
        for fact in _text_facts(page, order):
            facts.append(fact)
            order += 1
    snapshot.cache["rate_facts"] = facts
    return facts


_CERTIFICATE = tokens("certificate")[0]
_TERM_DEPOSITS = frozenset(tokens("certificate ira roth traditional", keep_stopwords=True))
_HEADER_NOISE = re.compile(r"\b(type|program|tier|term|balance|amount|product|category)\b", re.I)


def _label_words(page, row, term, values) -> frozenset:
    words = set(tokens(f"{row.section} {row.label} {_name_headers(row.header)}"))
    if _BUSINESS in page.url.lower():
        words.add(_BUSINESS)  # a business page's rows belong to business accounts
    # A deposit with a fixed term is a certificate, even under a label such as "Regular".
    if term and any(k in ("apy", "dividend") for k, _, _ in values) and not words & _TERM_DEPOSITS:
        words.add(_CERTIFICATE)
    return frozenset(words)


def _name_headers(header: tuple) -> str:
    """Words of the descriptive (non-rate) column names, without generic ones like "Type"."""
    names = [h for h in header if not metric_kind(h) and not _SKIP_COLUMN.search(h)]
    return _HEADER_NOISE.sub(" ", " ".join(names))


def _text_facts(page, order: int) -> list:
    """Rates written as "label: value" lines outside tables."""
    out = []
    for number, line in enumerate(page.text.splitlines()):
        stripped = plain(line.strip().lstrip("-* "))
        if not stripped or stripped.startswith("|") or len(stripped) > 170:
            continue
        found = _TEXT_RATE.match(stripped)
        if not found:
            continue
        label, name, value, tail = (found.group(g).strip() for g in ("label", "name", "value", "tail"))
        kind = _text_metric(f"{label} {name} {tail[:4]}")
        if not kind:
            continue
        described = f"{label} {name}"
        terms = term_months(described)
        out.append(RateFact(
            url=page.url, kind=page.kind, rank=page.rank, order=order + len(out),
            group=(page.url, "", label), label=label, name=name,
            label_words=frozenset(tokens(label)), name_words=frozenset(tokens(name)),
            values=((kind, kind, value),), effective="",
            term=terms[0] if terms else None, amount=None, model=model_year_range(described),
            expires=expiry_date(tail), line=number, carried=page.carried,
        ))
    return out


def _idf(sets) -> dict:
    counts = Counter(w for s in sets for w in s)
    total = max(1, len(sets))
    return {w: math.log(1 + total / c) for w, c in counts.items()}


def rate_context(snapshot) -> dict:
    cached = snapshot.cache.get("rate_context")
    if cached is not None:
        return cached
    facts = rate_facts(snapshot)
    groups = {f.group: f.label_words for f in facts}
    # Product names the site writes about: words of titles and headings that name a product type
    # ("Could an adjustable-rate mortgage help you buy a home today?", "Building With Construction Loans").
    topics = set()
    for page in snapshot.pages.values():
        headings = [page.title] + [plain(line) for line in page.text.splitlines() if line.startswith("#")]
        for heading in headings:
            words = set(tokens(heading))
            if words & _PRODUCT_TYPES:
                topics.update(w for w in words if w not in VERBS)
    context = {
        "label_idf": _idf(list(groups.values())),
        "name_idf": _idf([f.name_words for f in facts]),
        "vocabulary": set().union(*(f.words for f in facts)) if facts else set(),
        "topics": topics,
    }
    snapshot.cache["rate_context"] = context
    return context


@dataclass
class RateResult:
    facts: list = field(default_factory=list)
    sources: list = field(default_factory=list)
    declined: bool = False
    missing: str = ""             # product words with no rate on the site
    value_check: tuple = ()       # (percent, found)
    older: list = field(default_factory=list)  # lower-ranked facts with different values


def product_words(query) -> set:
    return {w for w in query.words if w not in _NOISE and not w.isdigit()}


def _fits(query, scored):
    """Rows that satisfy the question's term, model year and amount; None when nothing fits."""
    if query.terms:
        dated = [(s, f) for s, f in scored if f.term is not None]
        if dated:
            scored = [(s, f) for s, f in dated if f.term in query.terms]
            if not scored:
                return None
    if query.model_year:
        ranged = [(s, f) for s, f in scored if f.model]
        if ranged:
            scored = [(s, f) for s, f in ranged if f.model[0] <= query.model_year <= f.model[1]]
            if not scored:
                return None
    if query.amounts:
        target = sum(query.amounts[:2]) / len(query.amounts[:2])
        tiered = [(s, f) for s, f in scored if f.amount]
        if tiered:
            scored = [(s, f) for s, f in tiered if f.amount[0] <= target <= f.amount[1]]
            if not scored:
                return None
            tightest = min(f.amount[1] - f.amount[0] for _, f in scored)
            scored = [(s, f) for s, f in scored if f.amount[1] - f.amount[0] == tightest]
    return scored


def _variants(query, scored, keep_business: bool):
    asked = set(query.words)
    wanted = asked & VARIANTS
    if wanted:
        having = [(s, f) for s, f in scored if wanted <= f.words]
        if having:
            scored = having

    def is_variant(fact) -> bool:
        if (fact.words & VARIANTS) - asked:
            return True
        return not keep_business and _BUSINESS in fact.words and _BUSINESS not in asked

    plain_rows = [(s, f) for s, f in scored if not is_variant(f)]
    return plain_rows or scored


def _same_row_key(fact) -> tuple:
    """Identity of a row ignoring the page it is on and a "Business" prefix on its label."""
    label = re.sub(r"\bbusiness\b", "", norm(fact.label)).strip()
    return (label, norm(fact.name), tuple(norm(v) for _, _, v in fact.values))


def _dedupe(scored) -> list:
    seen, out = set(), []
    for s, f in scored:
        key = _same_row_key(f)
        if key in seen:
            continue
        seen.add(key)
        out.append((s, f))
    return out


def _effective_iso(fact) -> str:
    found = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", fact.effective or "")
    return f"{found.group(3)}-{int(found.group(1)):02d}-{int(found.group(2)):02d}" if found else ""


def _newest_effective(scored) -> list:
    """When equally ranked pages give one row different values, keep the most recently effective one."""
    best = {}
    for s, f in scored:
        row = (re.sub(r"\bbusiness\b", "", norm(f.label)).strip(), norm(f.name), _BUSINESS in f.words)
        if row not in best or _effective_iso(f) > _effective_iso(best[row][1]):
            best[row] = (s, f)
    keep = {id(f) for _, f in best.values()}
    return [(s, f) for s, f in scored if id(f) in keep]


def find_rates(query, snapshot) -> RateResult | None:
    """Rows answering a rate question, a decline, or None when no table is relevant."""
    facts = rate_facts(snapshot)
    words = product_words(query)
    if not facts or not words:
        return None
    context = rate_context(snapshot)
    missing = sorted(w for w in words if w not in context["vocabulary"] and w in context["topics"])
    if missing:
        return RateResult(declined=True, missing=" ".join(missing))
    scored = []
    for fact in facts:
        score = sum(context["label_idf"].get(w, 0.0) for w in words & fact.label_words)
        score += 0.6 * sum(context["name_idf"].get(w, 0.0) for w in (words & fact.name_words) - fact.label_words)
        if score > 0:
            scored.append((score, fact))
    if not scored:
        return None
    # An offer the page itself says has ended is stale; keep it only when nothing else matches.
    current = [(s, f) for s, f in scored if not (f.expires and f.expires < snapshot.taken_on)]
    scored = current or scored
    best = max(s for s, _ in scored)
    broad = query.wants_list or bool(query.percents)
    scored = [(s, f) for s, f in scored if s >= best * (_LIST_SHARE if broad else 0.95)]
    fitted = _fits(query, scored)
    if fitted is None:
        return RateResult(declined=True, missing="")
    scored = _variants(query, fitted, keep_business=broad)

    top = min(f.rank for _, f in scored)
    primary = [(s, f) for s, f in scored if f.rank == top]
    if broad and top == 0:
        covered = {norm(f.label) for _, f in primary}
        primary += [(s, f) for s, f in scored if f.rank == 1 and norm(f.label) not in covered]
    # Personal rows first, so a business row that only repeats them is dropped as a duplicate.
    primary = _dedupe(sorted(primary, key=lambda sf: (sf[1].rank, _BUSINESS in sf[1].words, sf[1].order)))
    primary = _newest_effective(primary)
    result = RateResult()

    if query.extreme:
        kind = _main_kind(query, [f for _, f in primary])
        ranked = [(f.number(kind), f) for _, f in primary if f.number(kind) is not None]
        if ranked:
            pick = max(ranked, key=lambda vf: (vf[0], -vf[1].order)) if query.extreme == "max" \
                else min(ranked, key=lambda vf: (vf[0], vf[1].order))
            result.facts = [pick[1]]
    elif query.percents and query.yes_no:
        kind = _main_kind(query, [f for _, f in primary])
        target = query.percents[0]
        hits = [f for _, f in primary if f.number(kind) is not None and abs(f.number(kind) - target) < 1e-9]
        result.value_check = (target, bool(hits))
        if hits:
            result.facts = hits
        else:
            first_group = primary[0][1].group
            group_rows = [f for _, f in primary if f.group == first_group and f.number(kind) is not None]
            if group_rows:
                result.facts = [max(group_rows, key=lambda f: (f.number(kind), -f.order))]
    elif query.wants_list:
        result.facts = [f for _, f in primary]
    else:
        top_score = max(s for s, _ in primary)
        groups = []
        for s, f in primary:
            if s >= top_score * 0.999 and f.group not in groups:
                groups.append(f.group)
        result.facts = [f for _, f in primary if f.group in groups]
    result.facts = result.facts[:_MAX_ROWS]
    if not result.facts:
        return None
    result.sources = _sources(result.facts, facts)
    result.older = _older(result.facts, [f for _, f in scored if f.rank > top])
    return result


def _main_kind(query, facts) -> str:
    have = {k for f in facts for k, _, _ in f.values}
    for kind in ((query.metric,) if query.metric in have else ()) + ("apy", "apr", "rate", "dividend"):
        if kind in have:
            return kind
    return ""


def _sources(chosen: list, all_facts: list) -> list:
    """Pages of the chosen rows first, then other official pages showing the same rows."""
    urls = []
    for fact in chosen:
        if fact.url not in urls:
            urls.append(fact.url)
    keys = {f.key() for f in chosen}
    for fact in all_facts:
        if fact.rank <= 1 and fact.key() in keys and fact.url not in urls:
            urls.append(fact.url)
    return urls[:8]


def _older(chosen: list, lower: list) -> list:
    """Lower-ranked rows with the same name as a chosen row but different values."""
    names = {norm(f.name or f.label): f for f in chosen}
    out = []
    for fact in lower:
        match = names.get(norm(fact.name or fact.label))
        if match and fact.key() != match.key():
            out.append(fact)
    return out


def shown_kinds(query, fact) -> list:
    have = [k for k, _, _ in fact.values]
    if query.metric == "apy" and "apy" in have:
        return ["apy"]
    if query.metric == "apr" and "apr" in have:
        return ["apr"]
    if query.metric == "dividend" and "dividend" in have:
        return ["dividend"] + (["apy"] if "apy" in have else [])
    return [k for k in _SHOW_ORDER if k in have]


def value_text(fact, kind: str) -> str:
    cell = fact.cell(kind).strip()
    label = _LABELS.get(kind, "")
    if kind in ("apr", "apy") and label.lower() in cell.lower():
        return cell
    return f"{cell} {label}".strip()


def row_text(query, fact) -> str:
    values = ", ".join(value_text(fact, k) for k in shown_kinds(query, fact))
    return f"{fact.name}: {values}" if fact.name else values


def format_rates(query, result: RateResult) -> str:
    groups = []
    for fact in result.facts:
        if not groups or groups[-1][0] != fact.group:
            groups.append((fact.group, [fact]))
        else:
            groups[-1][1].append(fact)
    parts = []
    for _, rows in groups:
        body = "; ".join(row_text(query, f) for f in rows)
        dates = {f.effective for f in rows}
        when = f" (effective {dates.pop()})" if len(dates) == 1 and "" not in dates else ""
        parts.append(f"{rows[0].label}: {body}{when}")
    text = ". ".join(parts) + "."
    if result.value_check:
        target, found = result.value_check
        if found:
            return f"Yes. {text}"
        top = result.facts[0] if result.facts else None
        tail = f" The highest {top.label} tier is {row_text(query, top)}." if top else ""
        return f"No. None of the matching rates on the rate sheet is {target:g}%.{tail}"
    return text
