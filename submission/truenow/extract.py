"""Answers drawn from passages rather than rate tables.

Passages are ranked by BM25, then weighted by how authoritative their page type
is and how many of the question's words they contain. News-type facts (counts,
events, awards) prefer the most recently published page, because press releases
keep older figures forever. Specialised readers handle links, branch hours,
branch addresses, counts and event dates; anything else returns the best
sentence (or list) of the best passage.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from .clean import links, plain
from .pages import month_number
from .text import tokens

_AUTHORITY = {0: 1.2, 1: 1.15, 2: 0.95, 3: 0.8, 4: 0.8, 5: 0.75}
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"“])|\s+(?=-\s+[A-Z0-9])")
_TIME = re.compile(r"\d{1,2}(?::\d{2})?\s*(?:a\.m\.|p\.m\.)|\bnoon\b", re.I)
_DAYS = r"(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?"
_DAY_LINE = re.compile(rf"^((?:{_DAYS})(?:\s*[-\u2013]\s*{_DAYS})?)\s*:\s*(.+)$", re.I)
_KIND = re.compile(r"^\*{0,2}(lobby|drive[- ]?up|drive[- ]?thru)\*{0,2}:?\s*(.*)$", re.I)
_ADDRESS = re.compile(r"\b\d{1,6}\s+[A-Z0-9][\w.' ]+,\s*[A-Z][\w.' ]+,\s*[A-Z]{2},?\s*\d{5}\b")
_FULL_DATE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{1,2}),\s+(\d{4})\b")
_PLACE = re.compile(r"\b((?:[A-Z][\w.'’-]*\s+){0,3}[A-Z][\w.'’-]*)\s+[Bb]ranch(?:es)?\b")
_PLACE_IN = re.compile(r"\bbranch(?:es)?\s+(?:in|at|on)\s+((?:[A-Z][\w.'-]*\s*){1,3})")
_GENERIC_PLACES = {"dupaco", "the", "a", "your", "our", "what", "which", "all", "any", "each", "every",
                   "do", "does", "is", "are", "can", "full-service", "where", "when", "will", "how",
                   "did", "has", "have", "branch"}
_EVENT_VERBS = re.compile(r"\b(clos\w*|open\w*|end\w*|launch\w*|begin\w*|start\w*|retir\w*|merg\w*)\b", re.I)
_DEFINITION = re.compile(r"^\s*(?:what|who)\s+(?:is|are|was|were)\b|^\s*tell me about\b|^\s*explain\b", re.I)
_HOW_MANY = re.compile(r"\bhow many\s+([\w\s'-]+?)\s+(?:do|does|did|are|is|were|was|have|has|had|can|will|"
                       r"used|use|work|worked|got|get|received|there)\b", re.I)
_COUNT_MODIFIERS = frozenset(tokens("full service total active physical retail open current dupaco",
                                    keep_stopwords=True))
_RELATIVE_YEAR = re.compile(r"\b(last|this|past|previous) year\b", re.I)
_EXPLICIT_YEAR = re.compile(r"\b(?:in|during|for)\s+(20\d{2})\b", re.I)
_APPLICATION_HOST = re.compile(r"^(join|apply|shine|homeloans|uploads|loans|online)\.", re.I)
_SALIENT = re.compile(r"\b\d{3}-\d{3}-\d{4}\b|\b\d{6,}\b|https?://\S+|\$\d[\d,]*(?:\.\d+)?")


@dataclass
class Found:
    text: str
    sources: list = field(default_factory=list)   # (url, quote)
    confident: bool = True


_LINK_ONLY = re.compile(r"^(?:[-*]\s+)?(?:\[[^\]]*\]\([^)]*\)\s*)+[>›]?$")


# Branch cards are short lines too, but an address or a phone number is an answer, not a menu.
_ADDRESS_LINE = re.compile(r"\b\d{1,6}\s+[A-Za-z0-9.' -]{2,40},\s*[A-Za-z .'-]{2,30},\s*[A-Z]{2}\b")
_PHONE_LINE = re.compile(r"\(?\b\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b")


def _navigation(text: str) -> bool:
    """Menu crumbs ("Login help Join Dupaco Dupaco.com") and bare link buttons carry no answer."""
    if _ADDRESS_LINE.search(text) or _PHONE_LINE.search(text):
        return False
    if " | " in text or re.search(r"\d+(?:\.\d+)?\s*%|\$\s?\d|\d{1,2}:\d{2}", text):
        return False  # table rows and short factual lines ("Lost debit card fee: $2.50")
    if _LINK_ONLY.match(text.strip()):
        return True
    items = re.split(r"(?:^|\s)[-*]\s+", text.strip())
    items = [item for item in items if item]
    if len(items) >= 3 and sum(item.startswith("[") for item in items) * 2 >= len(items):
        return True  # a list of links with blurbs ("Tools and resources")
    return len(re.findall(r"\b[a-z]{3,}\b", plain(text))) < 3


def ranked_passages(query, snapshot, limit: int = 40) -> list:
    """[(score, passage, page)] best first."""
    words = query.words
    if not words:
        return []
    asked = set(words)
    out = []
    for position, score in snapshot.index.search(words, limit=150):
        passage = snapshot.passages[position]
        if _navigation(passage.text):
            continue
        page = snapshot.pages[passage.url]
        present = asked & set(tokens(f"{passage.heading} {passage.text}"))
        coverage = sum(snapshot.index.idf(w) for w in present) / max(1e-9, sum(snapshot.index.idf(w) for w in asked))
        out.append((score * _AUTHORITY.get(page.rank, 0.75) * (0.4 + coverage), position, passage, page))
    out.sort(key=lambda item: (-item[0], item[1]))
    return [(s, p, page) for s, _, p, page in out[:limit]]


def sentences(text: str) -> list:
    return [s.strip(" -") for s in _SENTENCE.split(plain(text)) if s.strip(" -")]


def _sentence_score(query, sentence: str, index) -> float:
    asked = set(query.words)
    return sum(index.idf(w) for w in asked & set(tokens(sentence)))


def pick_sentences(query, text: str, index, lead: bool = False) -> list:
    """The best sentence of a passage, plus the next one when it continues the answer.

    For a lead paragraph (news releases, definitions) the opening two sentences
    are kept together when the best sentence is one of them.
    """
    parts = sentences(text)
    if not parts:
        return []
    statements = [i for i, s in enumerate(parts) if not s.endswith("?")] or list(range(len(parts)))
    best = max(statements, key=lambda i: (_sentence_score(query, parts[i], index), -i))
    if lead:
        return parts[:2] if best <= 1 else parts[best:best + 2]
    chosen = [best]
    bulleted = re.search(r"(^|\s)-\s+[A-Z0-9]", plain(text)) is not None
    if bulleted and len(parts) <= 8:
        return [p for p in parts if not p.endswith("?")][:8]
    following = best + 1
    if following < len(parts) and not parts[following].endswith("?"):
        rare = {w for w in query.words if index.idf(w) > 2.5}
        if _DEFINITION.search(query.text) or rare & set(tokens(parts[following])):
            chosen.append(following)
    return [parts[i] for i in chosen]


def _newest(candidates: list) -> list:
    return sorted(candidates, key=lambda c: (c[2].published or "0000", c[0]), reverse=True)


def corroborate(snapshot, text: str, first: str, limit: int = 5) -> list:
    """Other official pages that state the same distinctive value (phone, routing number, URL)."""
    values = [v.rstrip(".,;)") for v in _SALIENT.findall(text)]
    if not values:
        return []
    out = []
    for page in snapshot.pages.values():
        if page.rank > 1 or page.url == first:
            continue
        if any(v in page.text for v in values):
            out.append((page.url, ""))
        if len(out) >= limit:
            break
    return out


# --- places -----------------------------------------------------------------------------

def place_words(question: str) -> set:
    """Words naming a specific branch or town ("Mullan Avenue", "Galena", "Hy-Vee")."""
    words = set()
    for name in _PLACE.findall(question) + _PLACE_IN.findall(question):
        for word in re.findall(r"[A-Za-z][\w'-]*", name):
            if word.lower() not in _GENERIC_PLACES:
                words.add(word.lower())
    return words


def _mentions(text: str, words: set) -> bool:
    lower = text.lower()
    return all(re.search(rf"\b{re.escape(w)}\b", lower) for w in words)


# --- links ------------------------------------------------------------------------------

def _clean_url(url: str) -> str:
    parts = urlparse(url)
    if not parts.scheme:
        return url
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def find_link(query, snapshot) -> Found | None:
    asked = set(query.words) - set(tokens("link url website web page online see find use go view"))
    if not asked:
        return None
    application = bool(re.search(r"\b(online|apply|application|sign up|enroll)\b", query.text, re.I))
    best = None
    for page in snapshot.pages.values():
        if page.rank > 1:
            continue
        for anchor, url in links(page.text):
            if not url.startswith("http"):
                continue
            clean = _clean_url(url)
            parts = urlparse(clean)
            host = parts.netloc.lower()
            if snapshot.domain and host != snapshot.domain and not host.endswith("." + snapshot.domain):
                continue  # only the site's own addresses (no staging hosts or partner sites)
            target = snapshot.pages.get(clean) or snapshot.pages.get(clean.rstrip("/"))
            described = f"{anchor} {parts.netloc} {parts.path.replace('-', ' ').replace('/', ' ')}"
            if target:
                described += f" {target.title}"
            hit = asked & set(tokens(described))
            if not hit:
                continue
            in_url = len(asked & set(tokens(f"{parts.netloc} {parts.path.replace('-', ' ')}")))
            portal = 1 if application and _APPLICATION_HOST.match(parts.netloc) else 0
            score = (len(hit), portal, in_url, len(asked & set(tokens(anchor))), -page.rank)
            if best is None or score > best[0]:
                best = (score, clean, page.url, anchor)
    if not best:
        return None
    _, url, where, anchor = best
    return Found(f"You can use {url}", [(where, anchor)])


def locator_url(snapshot) -> str:
    cached = snapshot.cache.get("locator")
    if cached is not None:
        return cached
    url = ""
    for page in sorted(snapshot.pages.values(), key=lambda p: p.rank):
        for anchor, target in links(page.text):
            if re.search(r"\b(branch|location|locator|atm)", anchor, re.I) and "locator" in target:
                url = _clean_url(target)
                break
        if url:
            break
    snapshot.cache["locator"] = url
    return url


# --- branch hours -----------------------------------------------------------------------

@dataclass
class Schedule:
    url: str
    published: str
    kind: str      # "lobby" or "drive-up"
    lines: tuple   # ("Monday-Thursday: 8:30 a.m.-5 p.m.", ...)
    context: str   # text just before the schedule; names the branch


def schedules(snapshot) -> list:
    cached = snapshot.cache.get("schedules")
    if cached is not None:
        return cached
    found = []
    for page in snapshot.pages.values():
        lines = [plain(line) for line in page.text.splitlines()]
        kind, current, start = None, [], 0

        def close():
            if kind and current:
                context = " ".join(lines[max(0, start - 4):start])
                found.append(Schedule(page.url, page.published, kind, tuple(current), context))

        for i, line in enumerate(lines + [""]):
            marker = _KIND.match(line)
            if marker:
                close()
                kind, current, start = ("lobby" if marker.group(1).lower() == "lobby" else "drive-up"), [], i
                day = _DAY_LINE.match(marker.group(2)) if marker.group(2) else None
                if day:
                    current.append(f"{day.group(1)}: {day.group(2).strip()}")
                continue
            day = _DAY_LINE.match(line)
            if kind and day and (_TIME.search(day.group(2)) or "closed" in day.group(2).lower()):
                current.append(f"{day.group(1)}: {day.group(2).strip()}")
                continue
            if kind and line.strip():
                close()
                kind, current = None, []
    snapshot.cache["schedules"] = found
    return found


def find_hours(query, snapshot) -> Found | None:
    kind = "drive-up" if re.search(r"drive[- ]?(up|thru|through)", query.text, re.I) else "lobby"
    wanted = [s for s in schedules(snapshot) if s.kind == kind]
    places = place_words(query.text)
    if places:
        wanted = [s for s in wanted if _mentions(s.context + " " + s.url.replace("-", " "), places)]
        if not wanted:
            return Found("", [], confident=False)
    if not wanted:
        return None
    distinct, sources = [], []
    for schedule in sorted(wanted, key=lambda s: s.published or "0000", reverse=True):
        key = tuple(re.sub(r"\s+", "", line.lower().replace("\u2013", "-")) for line in schedule.lines)
        if key in [k for k, _ in distinct]:
            continue
        distinct.append((key, schedule))
        sources.append((schedule.url, "; ".join(schedule.lines)))
    label = "Drive-up" if kind == "drive-up" else "Lobby"
    locator = locator_url(snapshot)
    tail = f" For a specific branch, check the branch locator at {locator}." if locator and not places else ""
    if locator and not places:
        sources.append((locator, "branch locator"))
    if len(distinct) == 1:
        return Found(f"{label} hours: {'; '.join(distinct[0][1].lines)}.{tail}", sources)
    listed = " Another schedule: ".join("; ".join(s.lines) + "." for _, s in distinct[:5])
    opening = "No. " if query.yes_no else ""
    return Found(f"{opening}{label} hours vary by branch. One schedule: {listed}{tail}", sources)


# --- addresses --------------------------------------------------------------------------

def find_address(query, snapshot) -> Found | None:
    places = place_words(query.text)
    if not places:
        return None
    for page in sorted(snapshot.pages.values(), key=lambda p: p.rank):
        for line in page.text.splitlines():
            line = plain(line)
            match = _ADDRESS.search(line)
            if match and _mentions(line, places):
                return Found(match.group(0), [(page.url, line)])
    return None


# --- counts and events ------------------------------------------------------------------

def _count_nouns(question: str) -> list:
    """Content words of the phrase after "how many" ("branch locations" -> ["branch", "location"])."""
    found = _HOW_MANY.search(question)
    phrase = found.group(1) if found else re.sub(r"(?i).*\bhow many\b", "", question)
    words = tokens(phrase)
    return words[:3] if found else words[:1]


_ORDINAL_TIME = re.compile(
    r"\b(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth)\s+"
    r"(?:consecutive\s+)?time\b", re.I)


def find_ordinal(query, snapshot) -> Found | None:
    """ "How many times has X ..." answered from the newest "the fifth time X ..." statement."""
    key = key_words(query, snapshot) - set(tokens("time"))
    if not key:
        return None
    anchor = max(key, key=snapshot.index.idf)
    candidates = []
    for position, _ in snapshot.index.postings.get(anchor, ()):
        passage = snapshot.passages[position]
        page = snapshot.pages[passage.url]
        for sentence in sentences(passage.text):
            if _ORDINAL_TIME.search(sentence) and key <= set(tokens(sentence)):
                candidates.append((page.published or "0000", -page.rank, sentence, page))
    if not candidates:
        return None
    published, _, sentence, page = max(candidates, key=lambda c: (c[0], c[1]))
    when = f" (as stated on {_human(page.published)})" if page.published else ""
    return Found(f"{sentence}{when}", [(page.url, sentence)])


_PHONE = re.compile(r"\b\d{3}-\d{3}-\d{4}\b")


def find_phone(snapshot) -> Found | None:
    """The phone number official pages give most often."""
    counts, where = {}, {}
    for page in snapshot.pages.values():
        if page.rank > 1:
            continue
        for number in set(_PHONE.findall(page.text)):
            counts[number] = counts.get(number, 0) + 1
            where.setdefault(number, page.url)
    if not counts:
        return None
    number = max(counts, key=lambda n: (counts[n], n))
    return Found(f"You can call {number}.", [(where[number], number)])


def _year_asked(query, today: str):
    """(year, relative) for "in 2024" or "last year"; (None, False) when no year is asked."""
    explicit = _EXPLICIT_YEAR.search(query.text)
    if explicit:
        return int(explicit.group(1)), False
    relative = _RELATIVE_YEAR.search(query.text)
    if relative:
        year = int(today[:4])
        return (year if relative.group(1).lower() == "this" else year - 1), True
    return None, False


# Only "now" questions: "last year" asks for one year's figure, which the year match below handles.
# Not counts: money ("a $10,000 Nonprofit Grant") and tax codes ("a 501(c)(3) nonprofit").
_NOT_A_COUNT = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?(?:\s*(?:k|thousand|million|billion)\b)?|"
                          r"\b\d{3}\([a-z]\)\(\d+\)", re.I)
_RECENT = re.compile(r"\bthis year\b|\b(latest|most recent|newest|so far|currently)\b", re.I)


def find_count(query, snapshot, today: str = "") -> Found | None:
    """A count stated as "N <noun>", from the most recent page that states it.

    When the question asks about a year ("in 2024", "last year"), a statement
    about that year wins. If no statement covers a relative year, the newest
    figure is given with its year, followed by the one reported before it.
    """
    nouns = _count_nouns(query.text)
    if not nouns:
        return None
    if nouns[-1] == "time":
        return find_ordinal(query, snapshot)
    wanted = set(nouns)
    context = set(query.words) - wanted - set(tokens("how many number total last this past previous year"))
    positions = sorted({p for noun in wanted for p, _ in snapshot.index.postings.get(noun, ())})
    candidates = []
    for position in positions:
        passage = snapshot.passages[position]
        page = snapshot.pages[passage.url]
        for sentence in sentences(passage.text):
            words = tokens(_NOT_A_COUNT.sub(" ", sentence), keep_stopwords=True)
            for i, word in enumerate(words):
                if not word.isdigit():
                    continue
                gap = words[i + 1:i + 4]
                j = 0
                while j < len(gap) and gap[j] in _COUNT_MODIFIERS:
                    j += 1
                if j < len(gap) and gap[j] in wanted:
                    cover = sum(snapshot.index.idf(w) for w in context & set(words))
                    year = re.search(r"\b(?:in|during)\s+(20\d{2})\b", sentence, re.I)  # "In 2025, ..." too
                    candidates.append((cover, sentence, page, int(year.group(1)) if year else None))
                    break
    if not candidates:
        return None
    top = max(c[0] for c in candidates)
    # "this year", "latest": the newest dated release wins, so look wider before picking it.
    share = 0.6 if _RECENT.search(query.text) else 0.9
    close = [c for c in candidates if c[0] >= top * share]
    newest = sorted(close, key=lambda c: (c[2].published or "0000", c[3] or 0, -c[2].rank), reverse=True)
    target, relative = _year_asked(query, today or snapshot.taken_on)
    picked, earlier = newest[0], None
    if target:
        exact = [c for c in newest if c[3] == target]
        if exact:
            picked = exact[0]
        elif relative:
            earlier = next((c for c in newest[1:] if c[3] and picked[3] and c[3] < picked[3]), None)
    _, sentence, page, _ = picked
    sentence = re.sub(r"^It (has|is|was)\b", r"Dupaco \1", sentence)
    when = f" (as stated on {_human(page.published)})" if page.published else ""
    text = f"{sentence}{when}"
    sources = [(page.url, sentence)]
    if earlier:
        before = re.split(r",\s+(?=allowing|which|who|where|while)", earlier[1])[0].rstrip(".")
        text += f" The figure reported before that: {before}."
        sources.append((earlier[2].url, earlier[1]))
    return Found(text, sources)


def _human(day: str) -> str:
    months = ["January", "February", "March", "April", "May", "June", "July", "August",
              "September", "October", "November", "December"]
    y, m, d = day.split("-")
    return f"{months[int(m) - 1]} {int(d)}, {y}"


def find_event(query, snapshot, today: str) -> Found | None:
    """When something opens or closes, and whether it still is, from dated statements."""
    places = place_words(query.text)
    candidates = []
    for score, passage, page in ranked_passages(query, snapshot, limit=60):
        for sentence in sentences(passage.text):
            date = _FULL_DATE.search(sentence)
            if date and _EVENT_VERBS.search(sentence) and (not places or _mentions(f"{sentence} {page.title}", places)):
                candidates.append((score, sentence, page, date))
    if not candidates:
        return None
    top = max(c[0] for c in candidates)
    close = [c for c in candidates if c[0] >= top * 0.6]
    score, sentence, page, date = sorted(close, key=lambda c: (c[0], c[2].published), reverse=True)[0]
    month, day, year = month_number(date.group(1)), int(date.group(2)), int(date.group(3))
    event_day = f"{year:04d}-{month:02d}-{day:02d}"
    verb = _EVENT_VERBS.search(sentence).group(1).lower()
    stamp = date.group(0)
    subject = _subject(query, places)
    if verb.startswith("clos"):
        if event_day <= today:
            text = f"{'No. ' if query.yes_no else ''}{subject} closed on {stamp}."
        else:
            text = f"{'Yes, for now. ' if query.yes_no else ''}{subject} is scheduled to close on {stamp}."
    else:
        text = sentence
    return Found(text, [(page.url, sentence)])


def _subject(query, places: set) -> str:
    found = _PLACE.search(query.text)
    if found and places:
        name = re.sub(r"^(?:Is|Does|Did|When|Will|Has|What|Where)\s+", "", found.group(0))
        name = re.sub(r"^the\s+", "", name, flags=re.I)
        return f"The {name}"
    return "It"


def key_words(query, snapshot) -> set:
    """The rarest question word the site uses at all (for example "forbes")."""
    known = sorted(w for w in set(query.words) if snapshot.index.postings.get(w))
    if not known:
        return set()
    return {max(known, key=lambda w: (snapshot.index.idf(w), w))}


def _lead_passage(snapshot, page, key: set):
    """First passage on a page that mentions the key words (the definition, for "what is" questions)."""
    for position in snapshot.by_url.get(page.url, []):
        candidate = snapshot.passages[position]
        if key & set(tokens(candidate.text)) and not _navigation(candidate.text):
            return candidate
    return None


def find_passage(query, snapshot, prefer_new: bool = False) -> Found | None:
    ranked = ranked_passages(query, snapshot)
    if not ranked:
        return None
    key = key_words(query, snapshot)
    if prefer_new and key:
        top = ranked[0][0]
        pool = [r for r in ranked if r[0] >= top * 0.5 and key <= set(tokens(r[1].text))]
        ranked = _newest(pool) + ranked
    use_lead = bool(key) and (prefer_new or bool(_DEFINITION.search(query.text)))
    if use_lead:
        lead = _lead_passage(snapshot, ranked[0][2], key)
        if lead is not None:
            ranked = [(ranked[0][0], lead, ranked[0][2])] + ranked
    rare = {w for w in query.words if snapshot.index.idf(w) > 2.5}
    for position, (score, passage, page) in enumerate(ranked[:12]):
        chosen = pick_sentences(query, passage.text, snapshot.index, lead=use_lead and position == 0)
        if not chosen:
            continue
        text = " ".join(chosen)
        heading = plain(passage.heading)
        if heading and heading != page.title and (rare & set(tokens(heading))) - set(tokens(text)):
            text = f"{heading}: {text}"
        sources = [(page.url, text)] + corroborate(snapshot, text, page.url)
        return Found(text, sources)
    return None
