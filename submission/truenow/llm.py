"""Optional model reader on top of the rules, using the Gemini API (standard library only).

Switched on when GEMINI_API_KEY is set, in the environment or in a git-ignored .env file
at the repository root, unless TRUENOW_LLM=off. Without a key the system is the rules alone.

The rules still decide what counts as current: they pick the snapshot for the date, rank
pages by authority and gather the evidence. The model only reads that evidence and writes
the answer, and only for question types where it measured better than the rules (generic
passage answers, rate tables, links, phone numbers, and questions the rules declined).
Hours, counts, addresses, events, rate links and rate checks stay with the rules.

A model reply is kept only when every number in it appears in the evidence, the rules'
draft or the question. Otherwise, and on any error or timeout, the rules answer stands.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from .answer import refusal
from .contract_types import Answer, Source
from .extract import ranked_passages
from .query import analyze
from .rates import find_rates

DEFAULT_MODEL = "gemini-3.5-flash-lite"
API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
ATTEMPT_TIMEOUT_S = 4.0
TOTAL_BUDGET_S = 6.0
_RETRY_HTTP = {429, 500, 503}

# Readers the rules answer better than the model (measured on held-apart questions).
RULES_READERS = frozenset({"address", "count", "event", "hours", "rates_link", "rates_status",
                           "refusal_rate_not_stated", "refusal_nothing_found", "status_unlisted"})

KIND_NAMES = {
    "rate_sheet": "official rate sheet", "product": "product page", "document": "document or disclosure",
    "campaign": "promotion or landing page", "calculator": "calculator", "news": "news release",
    "article": "blog article",
}

SYSTEM = """You answer questions from members of a credit union. Use only the website excerpts you are given.

Rules:
1. Use only facts the excerpts state. If they do not state the answer, say you can't confirm it from the website and set "confident" to false. Never guess and never use outside knowledge.
2. The question is about the date given. When excerpts disagree, trust them in this order: official rate sheet, product page, document or disclosure, promotion or landing page, calculator, news release, blog article. For news-type facts (awards, counts of branches or members, events), the most recently published news release wins.
3. Ignore offers or promotions whose end date is before the question's date.
4. Answer exactly what was asked in one to three sentences. Give the value for the product, term, tier or amount the question describes. Do not add other tiers, older values, history, or numbers and dates that were not asked for.
5. Copy numbers, units, times and names exactly as the excerpt writes them (for example "5.39% APR", "8:30 a.m.").
6. If the question asks for a list, give every item the excerpts list.
7. Include a URL only when the question asks for a link, website or page.
8. Never ask the member a question back, never ask for personal details, and never add a phone number unless the question asks for one.

Return JSON with "evidence" (the ids of the excerpts you used), "answer" and "confident"."""

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "evidence": {"type": "ARRAY", "items": {"type": "STRING"}},
        "answer": {"type": "STRING"},
        "confident": {"type": "BOOLEAN"},
    },
    "required": ["evidence", "answer", "confident"],
    "propertyOrdering": ["evidence", "answer", "confident"],
}

# Same number reading as the kit's scorer: value plus "$" / "%" / no unit.
_NUM = re.compile(r"(\$)?\s*(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?\s*(%|percent)?", re.I)
_CANT = re.compile(
    r"\b(can(?:no|')t|cannot|unable to|not able to)\s+(?:confirm|find|determine|say|tell)\b|"
    r"\b(?:is|are)n't (?:listed|stated|mentioned)|\bnot (?:listed|stated|mentioned|specified)\b|"
    r"\bdoes(?:n't| not) (?:say|state|list|mention|specify)\b|"
    r"\b(i (?:do not|don't|can't|cannot) (?:know|find|confirm)|not (?:sure|able to confirm)|"
    r"no (?:longer )?(?:available|offered)|isn't listed|is not listed|please (?:contact|call|visit)|"
    r"no information)\b", re.I)
_PLACEHOLDER = re.compile(r"<val|\*\*\*|\{[a-z_]+\}", re.I)
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]*)[^)]*\)")

MAX_PAGES = 6
MAX_WINDOWS_PER_PAGE = 3
MAX_CHARS = 12000
MAX_WINDOW_CHARS = 2600


def numbers(text: str) -> set:
    out = set()
    for m in _NUM.finditer(text or ""):
        unit = "$" if m.group(1) else ("%" if m.group(4) else "")
        out.add((unit, round(float(m.group(2).replace(",", "") + ("." + m.group(3) if m.group(3) else "")), 4)))
    return out


def _key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key
    env = Path(__file__).resolve().parents[2] / ".env"
    try:
        for line in env.read_text(encoding="utf-8").splitlines():
            name, _, value = line.partition("=")
            if name.strip() == "GEMINI_API_KEY":
                return value.strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


# ---- evidence ---------------------------------------------------------------------------

def _tidy(text: str, keep_urls: bool) -> str:
    text = _IMAGE.sub("", text)
    if keep_urls:
        text = _LINK.sub(lambda m: f"{m.group(1)} ({m.group(2)})" if m.group(1).strip() else m.group(2), text)
    else:
        text = _LINK.sub(lambda m: m.group(1), text)
    text = re.sub(r"\*\*|__", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _window(lines: list, line: int) -> tuple:
    """Line span to show around a passage starting at `line`; tables come whole with their labels."""
    if 0 <= line < len(lines) and lines[line].lstrip().startswith("|"):
        top = line
        while top > 0 and lines[top - 1].lstrip().startswith("|"):
            top -= 1
        bottom = line
        while bottom + 1 < len(lines) and lines[bottom + 1].lstrip().startswith("|"):
            bottom += 1
        return max(0, top - 3), min(len(lines) - 1, bottom + 4), (top, line)
    return max(0, line - 3), min(len(lines) - 1, line + 14), None


def _render(lines: list, start: int, end: int, table, keep_urls: bool) -> str:
    chosen = list(range(start, end + 1))
    if table and len(chosen) > 40:  # long table: labels and header, then rows near the asked row
        top, row = table
        head = [i for i in chosen if i <= top + 1]
        near = [i for i in chosen if abs(i - row) <= 15 and i > top + 1]
        tail = [i for i in chosen if i > row + 15 and not lines[i].lstrip().startswith("|")]
        chosen = sorted(set(head + near + tail))
    return _tidy("\n".join(lines[i] for i in chosen), keep_urls)[:MAX_WINDOW_CHARS]


def _quote_line(page, quote: str) -> int:
    probe = _tidy(quote or "", False)[:40].lower()
    if probe:
        for number, line in enumerate(page.text.splitlines()):
            if probe[:25] in _tidy(line, False).lower():
                return number
    return 0


def build_pack(library, query, rules_sources: list) -> dict:
    """Excerpts for the model: pages the rules cited first, then rate rows, then ranked passages."""
    snapshot = library.at(query.day)
    today = query.day or library.latest.taken_on
    seeds = []
    for url, quote in rules_sources:
        page = snapshot.pages.get(url)
        if page is not None:
            seeds.append((url, _quote_line(page, quote)))
    if query.is_rate:
        rates = find_rates(query, snapshot)
        if rates is not None:
            seeds += [(f.url, f.line) for f in rates.facts[:6]]
    seeds += [(p.url, p.line) for _, p, _ in ranked_passages(query, snapshot, limit=40)]

    order, spans = [], {}
    for url, line in seeds:
        page = snapshot.pages.get(url)
        if page is None:
            continue
        if url not in spans:
            if len(order) >= MAX_PAGES:
                continue
            order.append(url)
            spans[url] = []
        if len(spans[url]) < MAX_WINDOWS_PER_PAGE:
            spans[url].append((line, _window(page.text.splitlines(), line)))

    excerpts, used = [], 0
    for url in order:
        page = snapshot.pages[url]
        lines = page.text.splitlines()
        merged = []
        for _, (start, end, table) in sorted(spans[url], key=lambda s: s[1][0]):
            if merged and start <= merged[-1][1] + 2 and not (table or merged[-1][2]):
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]), None)
            elif merged and start <= merged[-1][1] + 2 and table and merged[-1][2] == table:
                continue
            else:
                merged.append((start, end, table))
        text = "\n\n[...]\n\n".join(_render(lines, s, e, t, query.wants_link) for s, e, t in merged)
        room = MAX_CHARS - used
        if not text:
            continue
        if room < 400:
            break
        text = text[:room]
        used += len(text)
        excerpts.append({"id": f"E{len(excerpts) + 1}", "url": url, "title": page.title, "kind": page.kind,
                         "fetched": page.fetched, "published": page.published, "carried": page.carried,
                         "text": text})
    return {"today": today, "snapshot": snapshot.snapshot_id, "excerpts": excerpts}


def user_prompt(question: str, pack: dict, draft: str = "") -> str:
    parts = [f"Question: {question}",
             f"Date the question is about: {pack['today']} (answer with what the website said then)"]
    if draft:
        parts.append(f"Draft from a keyword search, may be wrong or incomplete: {draft}")
    parts.append("Website excerpts:")
    for e in pack["excerpts"]:
        meta = [e["title"] or "(untitled)", e["url"], KIND_NAMES.get(e["kind"], e["kind"]), f"crawled {e['fetched']}"]
        if e["published"]:
            meta.append(f"published {e['published']}")
        if e["carried"]:
            meta.append("page missing from this crawl, text from an earlier crawl")
        parts.append(f"[{e['id']}] " + " | ".join(meta) + "\n" + e["text"])
    return "\n\n".join(parts)


# ---- model call ---------------------------------------------------------------------------

class ModelReader:
    def __init__(self, key: str, model: str = DEFAULT_MODEL):
        self.key = key
        self.model = model

    @classmethod
    def from_env(cls):
        """A reader when a key is available and the layer is not switched off, else None."""
        if os.environ.get("TRUENOW_LLM", "").strip().lower() in ("off", "0", "false", "no"):
            return None
        key = _key()
        if not key:
            return None
        return cls(key, os.environ.get("GEMINI_MODEL", "").strip() or DEFAULT_MODEL)

    def _ask(self, prompt: str):
        body = json.dumps({
            "systemInstruction": {"parts": [{"text": SYSTEM}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0, "seed": 7, "responseMimeType": "application/json",
                                 "responseSchema": SCHEMA},
        }).encode()
        deadline = time.monotonic() + TOTAL_BUDGET_S
        for attempt in (1, 2):
            remaining = deadline - time.monotonic()
            if remaining < 0.5:
                return None
            request = urllib.request.Request(API.format(model=self.model), data=body,
                                             headers={"x-goog-api-key": self.key, "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=min(ATTEMPT_TIMEOUT_S, remaining)) as response:
                    data = json.load(response)
                parts = data["candidates"][0]["content"]["parts"]
                return json.loads("".join(p.get("text", "") for p in parts if not p.get("thought")))
            except urllib.error.HTTPError as error:
                if error.code not in _RETRY_HTTP or attempt == 2:
                    return None
                time.sleep(0.3)
            except Exception:  # timeout, network, malformed reply: the rules answer stands
                return None
        return None

    def wanted(self, reader: str, query) -> bool:
        if reader in RULES_READERS:
            return False
        if reader == "rates" and query.percents:  # "Does any tier pay 0.35%?": the rate sheet decides
            return False
        return True

    def answer(self, library, question: str, as_of, rules: Answer, reader: str):
        """A better Answer, or None to keep the rules answer."""
        query = analyze(question, as_of)
        if not self.wanted(reader, query):
            return None
        cited = [(s.url, s.quote or "") for s in rules.sources or []]
        pack = build_pack(library, query, cited)
        if not pack["excerpts"]:
            return None
        data = self._ask(user_prompt(question, pack, rules.text if rules.confident else ""))
        if not isinstance(data, dict):
            return None
        text = str(data.get("answer", "")).strip()
        if not data.get("confident") or not text:
            return refusal()
        evidence = "\n".join(e["text"] for e in pack["excerpts"]) + "\n" + rules.text + "\n" + question
        if not numbers(text) <= numbers(evidence) or _PLACEHOLDER.search(text):
            return None  # a figure the evidence does not contain: keep the rules answer
        if _CANT.search(text):
            return refusal()
        by_id = {e["id"]: e["url"] for e in pack["excerpts"]}
        urls = [by_id[i] for i in data.get("evidence", []) if isinstance(i, str) and i in by_id]
        urls += [s.url for s in rules.sources or []]
        sources = [Source(url=u, snapshot_id=pack["snapshot"]) for u in dict.fromkeys(urls)]
        return Answer(text=text, sources=sources, confident=True, conflict_note=rules.conflict_note)
