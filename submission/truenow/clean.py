"""Cleanup for crawled markdown pages.

The crawl keeps widget text, escaped line breaks and footnote markers glued to
numbers ("4.10%2", "5.39% APR1"). Removing them keeps values comparable across
pages and keeps answers free of artifacts.
"""
from __future__ import annotations

import re

# Text injected by site widgets. It carries no content and changes between crawls.
_WIDGET_LINES = frozenset({
    "✕",  # close button glyph
    "Third Party Plugins",
    "Messenger Launcher",
    "Messenger",
    "Loading locations...",
})
_CONTINUE_CANCEL = re.compile(r"^\[Continue\]\([^)]*\)\s*Cancel$")
_ESCAPED_BREAK = re.compile(r"(?:\\+r)?\\+n")
_SUPERSCRIPT = re.compile("[¹²³]")
_FOOTNOTE_AFTER = re.compile(r"(%|\bAPR|\bAPY|\(APY\))[0-9]{1,2}(?=[\s|,.;:)]|$)")
_FOOTNOTE_LEAD = re.compile(r"^[0-9](?=[A-Z])")
_ESCAPED_MARK = re.compile(r"\\([*_])")


def clean_line(line: str) -> str:
    """Strip footnote markers and markdown escapes from one line."""
    line = _SUPERSCRIPT.sub("", line)
    line = _FOOTNOTE_AFTER.sub(r"\1", line)
    line = _FOOTNOTE_LEAD.sub("", line)
    return _ESCAPED_MARK.sub(r"\1", line).rstrip()


def clean_markdown(text: str) -> str:
    """Return page text without widget lines, escaped breaks or footnote markers."""
    text = _ESCAPED_BREAK.sub("\n", text)
    out = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped in _WIDGET_LINES or _CONTINUE_CANCEL.match(stripped):
            continue
        out.append(clean_line(raw))
    return "\n".join(out)


# Related-article lists that pages append after their own content.
_TEASER_HEADINGS = ("## Handpicked for You",)


def strip_teasers(text: str) -> str:
    for heading in _TEASER_HEADINGS:
        cut = text.find(heading)
        if cut != -1:
            text = text[:cut]
    return text


_LINK = re.compile(r"!?\[([^\]]*)\]\(([^)\s]*)[^)]*\)")


def links(text: str) -> list:
    """(anchor text, url) for every markdown link or image in the text."""
    return [(anchor.strip(), url) for anchor, url in _LINK.findall(text)]


def plain(text: str) -> str:
    """Text with markdown links reduced to their anchor text and images removed."""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = _LINK.sub(lambda m: m.group(1), text)
    text = re.sub(r"\*\*|__", "", text)
    return re.sub(r"[ \t]+", " ", text).strip()
