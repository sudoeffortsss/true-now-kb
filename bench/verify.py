"""Check a question set against the snapshots before trusting it.

For every item: each `must_include` value must appear on some page of the
snapshot the question is about (the newest one taken on or before `as_of`), and
each `must_not_include` value should appear somewhere in the data, so the trap
is real. Run from the repo root:

    python bench/verify.py bench/dev.jsonl
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "submission"))

from run_eval import load_snapshots  # noqa: E402
from scoring import contains  # noqa: E402
from truenow.clean import clean_markdown  # noqa: E402
from truenow.pages import page_type  # noqa: E402


def main(path: str) -> int:
    snapshots = load_snapshots(ROOT / "snapshots")
    texts = [{p.url: clean_markdown(p.markdown) for p in s.pages} for s in snapshots]
    problems = 0
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        day = item.get("as_of")
        index = max((i for i, s in enumerate(snapshots) if not day or s.taken_on <= day), default=0)
        notes = []
        for token in item["must_include"]:
            pages = [u for u, t in texts[index].items() if contains(t, token)]
            kinds = Counter(page_type(u) for u in pages)
            if not pages:
                notes.append(f"MISSING {token!r} in {snapshots[index].snapshot_id}")
                problems += 1
            else:
                notes.append(f"{token!r}: {dict(kinds)}")
        for token in item["must_not_include"]:
            seen = [s.snapshot_id[:2] for s, t in zip(snapshots, texts) if any(contains(x, token) for x in t.values())]
            notes.append(f"trap {token!r} seen in {seen or 'no snapshot'}")
        if item["category"] == "should_refuse":
            notes.append("decline expected (checked by hand)")
        print(f"{item['id']} [{item['category']}] {item['question']}")
        for note in notes:
            print(f"    {note}")
    print(f"\n{problems} missing value(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "bench/dev.jsonl"))
