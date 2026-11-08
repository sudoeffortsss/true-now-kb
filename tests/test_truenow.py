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
        for name in ("hours", "count", "rates_link", "refusal_personal"):
            self.assertFalse(reader.wanted(name, analyze("What are the lobby hours?")))
        for name in ("address", "event"):
            self.assertTrue(reader.wanted(name, analyze("Where is the Peosta branch?")))
        self.assertTrue(reader.wanted("passage", analyze("Can I open an account online?")))
        self.assertTrue(reader.wanted("rates", analyze("What is the 12-month certificate APY?")))
        self.assertFalse(reader.wanted("rates", analyze("Does any savings tier pay 0.35% APY?")))

    def test_switched_off_or_without_key(self):
        self.assertIsNone(llm.ModelReader.from_env())  # TRUENOW_LLM=off in this test run


class _Obj:
    def __init__(self, **fields):
        self.__dict__.update(fields)


class _FakeAnthropic:
    """Stands in for anthropic.Anthropic(): records the request, returns a canned reply."""

    def __init__(self, stop_reason="end_turn", text='{"evidence": ["E1"], "answer": "It is $2.50.", "confident": true}',
                 beta_error=None):
        self.requests, self.beta_requests, self.beta_error = [], [], beta_error
        self.messages = self
        self.beta = _Obj(messages=_Obj(create=self._beta_create))
        self._reply = _Obj(stop_reason=stop_reason, content=[_Obj(type="text", text=text)],
                           usage=_Obj(input_tokens=1000, output_tokens=20))

    def create(self, **request):
        self.requests.append(request)
        return self._reply

    def _beta_create(self, **request):
        self.beta_requests.append(request)
        if self.beta_error:
            raise self.beta_error
        return self._reply


class _ParameterRejected(Exception):
    status_code = 400


class _FakeOpenAI:
    """Stands in for openai.OpenAI(): rejects the parameters named in `refuse`."""

    def __init__(self, refuse=()):
        self.requests, self.refuse = [], set(refuse)
        self.chat = _Obj(completions=self)

    def create(self, **request):
        self.requests.append(request)
        for name in self.refuse:
            if name in request:
                raise _ParameterRejected(f"Unsupported parameter: '{name}'")
        message = _Obj(content='{"evidence": ["E2"], "answer": "It is $2.50.", "confident": true}')
        return _Obj(choices=[_Obj(finish_reason="stop", message=message)],
                    usage=_Obj(prompt_tokens=1000, completion_tokens=20))


class Providers(unittest.TestCase):
    """Choosing a provider and calling it, with fake SDK clients (no network, no SDK installed)."""

    NAMES = ("TRUENOW_LLM", "TRUENOW_LLM_PROVIDER", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
             "GEMINI_MODEL", "ANTHROPIC_MODEL", "OPENAI_MODEL")

    def setUp(self):
        self.saved = {n: os.environ.pop(n, None) for n in self.NAMES}
        self.saved_env_file = llm.ENV_FILE
        llm.ENV_FILE = Path(os.devnull)  # ignore a developer's local .env

    def tearDown(self):
        for name, value in self.saved.items():
            os.environ.pop(name, None)
            if value is not None:
                os.environ[name] = value
        llm.ENV_FILE = self.saved_env_file

    def test_no_key_means_rules_only(self):
        self.assertIsNone(llm.ModelReader.from_env())

    def test_the_key_present_picks_the_provider(self):
        os.environ["ANTHROPIC_API_KEY"] = "a"
        reader = llm.ModelReader.from_env()
        self.assertEqual((reader.provider, reader.model), ("anthropic", llm.PROVIDERS["anthropic"][2]))
        os.environ["OPENAI_API_KEY"] = "o"  # several keys: the first provider in PROVIDERS wins
        self.assertEqual(llm.ModelReader.from_env().provider, "openai")
        os.environ["GEMINI_API_KEY"] = "g"
        self.assertEqual(llm.ModelReader.from_env().provider, "gemini")

    def test_provider_and_model_can_be_chosen(self):
        os.environ.update(GEMINI_API_KEY="g", OPENAI_API_KEY="o", TRUENOW_LLM_PROVIDER="openai",
                          OPENAI_MODEL="some-model")
        reader = llm.ModelReader.from_env()
        self.assertEqual((reader.provider, reader.model), ("openai", "some-model"))

    def test_claude_request_turns_thinking_off_and_asks_for_json(self):
        reader = llm.ModelReader("k", "claude-haiku-5-5", provider="anthropic")
        reader._client = fake = _FakeAnthropic()
        self.assertEqual(reader._ask("prompt")["answer"], "It is $2.50.")
        request = fake.requests[0]
        self.assertEqual(request["thinking"], {"type": "disabled"})
        self.assertEqual(request["output_config"]["format"]["type"], "json_schema")
        self.assertEqual(request["system"], llm.SYSTEM)
        self.assertNotIn("temperature", request)

    def test_claude_refusal_or_cut_off_keeps_the_rules_answer(self):
        for stop in ("refusal", "max_tokens"):
            reader = llm.ModelReader("k", "claude-haiku-5-5", provider="anthropic")
            reader._client = _FakeAnthropic(stop_reason=stop)
            self.assertIsNone(reader._ask("prompt"))

    def test_opus_asks_for_the_server_side_refusal_fallback(self):
        reader = llm.ModelReader("k", "claude-opus-5-5", provider="anthropic")
        reader._client = fake = _FakeAnthropic()
        self.assertIsNotNone(reader._ask("prompt"))
        self.assertEqual(fake.beta_requests[0]["fallbacks"], "default")
        self.assertEqual(fake.beta_requests[0]["betas"], [llm._FALLBACK_BETA])
        self.assertEqual(fake.requests, [])

    def test_a_rejected_fallback_is_dropped_for_the_rest_of_the_run(self):
        reader = llm.ModelReader("k", "claude-opus-5-5", provider="anthropic")
        reader._client = fake = _FakeAnthropic(beta_error=_ParameterRejected("fallbacks is not available"))
        self.assertIsNotNone(reader._ask("prompt"))
        self.assertFalse(reader._claude_fallbacks)
        reader._ask("again")
        self.assertEqual((len(fake.beta_requests), len(fake.requests)), (1, 2))

    def test_claude_models_without_an_off_switch_get_low_effort(self):
        self.assertEqual(llm._claude_settings("claude-opus-5-5"), (None, "low"))
        self.assertEqual(llm._claude_settings("claude-sonnet-5-5"), ({"type": "between_tools"}, None))

    def test_openai_request_uses_strict_json(self):
        reader = llm.ModelReader("k", "m", provider="openai")
        reader._client = fake = _FakeOpenAI()
        self.assertEqual(reader._ask("prompt")["evidence"], ["E2"])
        self.assertTrue(fake.requests[0]["response_format"]["json_schema"]["strict"])

    def test_openai_finds_the_parameters_a_model_accepts(self):
        reader = llm.ModelReader("k", "m", provider="openai")
        reader._client = fake = _FakeOpenAI(refuse=("reasoning_effort",))
        self.assertIsNotNone(reader._ask("prompt"))
        self.assertEqual(reader._openai_shape, {"temperature": 0, "seed": 7})
        reader._ask("prompt again")  # the shape that worked is reused, no more probing
        self.assertEqual(len(fake.requests), 4)

    def test_sdk_errors_keep_the_rules_answer(self):
        reader = llm.ModelReader("k", "claude-haiku-5-5", provider="anthropic")

        class Broken:
            messages = None
        reader._client = Broken()
        self.assertIsNone(reader._ask("prompt"))


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
