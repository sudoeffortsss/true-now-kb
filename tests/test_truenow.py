"""Unit tests. They need no website snapshots, network or API key:

    python -m unittest discover -s tests

The end-to-end cases use the kit's two-snapshot toy site in harness/selftest.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "harness"), str(ROOT / "submission")]
os.environ["TRUENOW_LLM"] = "off"  # these tests never call the model API

from run_eval import load_snapshots  # noqa: E402  (kit)
from scoring import _numbers, score_conflicts, score_item  # noqa: E402  (kit)
from truenow import llm  # noqa: E402
from truenow.answer import REFUSAL, respond_traced  # noqa: E402
from truenow.contract_types import Answer, Source  # noqa: E402
from truenow.pages import RANK, page_type, published_date  # noqa: E402
from truenow.query import analyze  # noqa: E402
from truenow.store import Library  # noqa: E402
from truenow.system import build  # noqa: E402
from truenow.tables import parse_tables  # noqa: E402

TOY = ROOT / "harness" / "selftest"


def toy_system():
    system = build()
    for snapshot in load_snapshots(TOY / "snapshots"):
        system.ingest(snapshot)
    return system


class PageTypes(unittest.TestCase):
    def test_type_comes_from_the_url(self):
        cases = {
            "https://example-cu.test/rates/certificates": "rate_sheet",
            "https://example-cu.test/business-rates": "rate_sheet",
            "https://example-cu.test/auto-loans": "product",
            "https://example-cu.test/fee-schedule.pdf": "document",
            "https://example-cu.test/checking-offer-lp": "campaign",
            "https://example-cu.test/calculators/auto-loan": "calculator",
            "https://example-cu.test/blog/save-more": "article",
        }
        for url, kind in cases.items():
            with self.subTest(url):
                self.assertEqual(page_type(url), kind)

    def test_authority_order(self):
        order = ["rate_sheet", "product", "document", "campaign", "calculator", "article"]
        self.assertEqual(sorted(order, key=RANK.get), order)
        self.assertEqual(RANK["news"], RANK["calculator"])


class Dates(unittest.TestCase):
    def test_published_date_from_url_or_first_date_line(self):
        self.assertEqual(published_date("https://x.test/2026/05/01/branch-news", ""), "2026-05-01")
        self.assertEqual(published_date("https://x.test/news/award", "Title\nMay 4, 2026\nBody"), "2026-05-04")
        self.assertEqual(published_date("https://x.test/news/award", "No date here"), "")

    def test_date_written_in_the_question(self):
        self.assertEqual(analyze("What was the 6-month certificate APY in January 2026?").day, "2026-01-31")
        self.assertEqual(analyze("What is the rate as of March 3, 2026?").day, "2026-03-03")
        self.assertIsNone(analyze("What is the APY now?").day)
        self.assertEqual(analyze("What is the APY?", "2026-02-10").day, "2026-02-10")

    def test_snapshot_is_the_newest_on_or_before_the_day(self):
        library = Library()
        for day in ("2026-05-01", "2026-09-28", "2026-07-15"):
            library.add(SimpleNamespace(taken_on=day))
        self.assertEqual(library.at(None).taken_on, "2026-09-28")
        self.assertEqual(library.at("2026-07-31").taken_on, "2026-07-15")
        self.assertEqual(library.at("2026-07-15").taken_on, "2026-07-15")
        self.assertEqual(library.at("2026-01-01").taken_on, "2026-05-01")


class Tables(unittest.TestCase):
    PAGE = "\n".join([
        "Certificates",
        "Special Certificates",
        "| Term | Dividend Rate | APY 1 |",
        "| --- | --- | --- |",
        "| 6 Month | 3.45% | 3.50% |",
        "| 12 Month | 3.35% | 3.40% |",
        "",
        "Rates are effective as of 09/01/2026",
    ])

    def test_rows_keep_labels_header_and_effective_date(self):
        rows = parse_tables(self.PAGE)
        self.assertEqual(len(rows), 2)
        first = rows[0]
        self.assertEqual((first.section, first.label), ("Certificates", "Special Certificates"))
        self.assertEqual(first.header, ("Term", "Dividend Rate", "APY"))  # footnote marker dropped
        self.assertEqual(first.cell("apy"), "3.50%")
        self.assertEqual(first.effective, "09/01/2026")


class ModelGuards(unittest.TestCase):
    """The model layer with a stubbed reply: what is kept, refused or thrown away."""

    @classmethod
    def setUpClass(cls):
        cls.system = toy_system()
        cls.question = "How much is a lost debit card replacement?"
        cls.rules, cls.reader = respond_traced(cls.system.library, cls.question)

    def reply(self, data):
        reader = llm.ModelReader("test-key")
        reader._ask = lambda prompt: data
        return reader.answer(self.system.library, self.question, None, self.rules, self.reader)

    def test_numbers_match_the_kit_scorer(self):
        for text in ("5.39% APR", "$1,250.00 fee", "8:30 a.m.", "60-month term", "0.35 percent"):
            with self.subTest(text):
                self.assertEqual(llm.numbers(text), _numbers(text))

    def test_grounded_reply_is_kept_with_the_rules_sources(self):
        answer = self.reply({"evidence": ["E1"], "answer": "Replacing a lost debit card costs $2.50.", "confident": True})
        self.assertIsNotNone(answer)
        self.assertTrue(answer.confident)
        self.assertLessEqual({s.url for s in self.rules.sources}, {s.url for s in answer.sources})

    def test_reply_with_a_number_not_in_the_evidence_is_dropped(self):
        self.assertIsNone(self.reply({"evidence": ["E1"], "answer": "It costs $9.99.", "confident": True}))

    def test_unsure_reply_becomes_a_refusal(self):
        for data in ({"evidence": [], "answer": "Not stated.", "confident": False},
                     {"evidence": ["E1"], "answer": "I can't confirm that from the website.", "confident": True}):
            with self.subTest(data["answer"]):
                answer = self.reply(data)
                self.assertFalse(answer.confident)
                self.assertEqual(answer.text, REFUSAL)

    def test_figure_question_needs_a_figure(self):
        vague = self.reply({"evidence": ["E1"], "answer": "It depends on the card.", "confident": True})
        self.assertEqual(vague.text, REFUSAL)
        free = self.reply({"evidence": ["E1"], "answer": "Replacing a lost debit card is free.", "confident": True})
        self.assertTrue(free.confident)

    def test_unusable_reply_keeps_the_rules_answer(self):
        self.assertIsNone(self.reply(None))
        self.assertIsNone(self.reply({"evidence": ["E1"], "answer": "It is <value>***", "confident": True}))

    def test_routing(self):
        reader = llm.ModelReader("test-key")
        for name in ("hours", "count", "address", "event", "rates_link"):
            self.assertFalse(reader.wanted(name, analyze("What are the lobby hours?")))
        self.assertTrue(reader.wanted("passage", analyze("Can I open an account online?")))
        self.assertTrue(reader.wanted("rates", analyze("What is the 12-month certificate APY?")))
        self.assertFalse(reader.wanted("rates", analyze("Does any savings tier pay 0.35% APY?")))

    def test_switched_off_or_without_key(self):
        self.assertIsNone(llm.ModelReader.from_env())  # TRUENOW_LLM=off in this test run


class ToySite(unittest.TestCase):
    """End to end on the kit's toy site, rules only."""

    @classmethod
    def setUpClass(cls):
        cls.system = toy_system()
        cls.items = [json.loads(l) for l in (TOY / "eval.jsonl").read_text().splitlines() if l.strip()]

    def test_every_question_scores_full_marks(self):
        for item in self.items:
            with self.subTest(item["id"]):
                answer = self.system.answer(item["question"], item.get("as_of"))
                self.assertEqual(score_item(item, answer)["score"], 1.0, answer.text)

    def test_refusal_has_no_numbers_and_is_not_confident(self):
        answer = self.system.answer("What is the 60-month jumbo certificate rate?")
        self.assertFalse(answer.confident)
        self.assertEqual(_numbers(answer.text), set())

    def test_own_account_data_is_refused(self):
        answer, reader = respond_traced(self.system.library, "What's my current account balance?")
        self.assertEqual(reader, "refusal_personal")
        self.assertFalse(answer.confident)

    def test_repeated_question_gives_the_same_answer(self):
        first = self.system.answer("What is the 6-month certificate APY?")
        self.assertIs(first, self.system.answer("What is the 6-month certificate APY?"))

    def test_conflict_list_matches_the_gold(self):
        gold = json.loads((TOY / "conflicts_gold.json").read_text())
        report = score_conflicts(gold, self.system.conflicts())
        self.assertEqual((report["f1"], report["resolution_accuracy"]), (1.0, 1.0))


if __name__ == "__main__":
    unittest.main()
