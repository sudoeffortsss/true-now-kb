# Notes

## Approach

The system answers from the five snapshots only. A rules core decides what is current and gathers the evidence. An optional model reader (Gemini, used only when a key is set) then writes the answer from that evidence, for the question types where it measured better than the rules. Everything is in `submission/truenow/` and uses the Python standard library only.

**Rules core.**

- *Ingest.* Each page is cleaned: site widgets, footnote markers and the "Handpicked for You" teasers are removed. The URL gives the page type, in authority order: rate sheet, product page, document, promotion page, calculator, news release, blog article. Markdown tables become rate facts, each with its product, section, term, balance tier, model years, every APY, APR and dividend cell, the "effective as of" date, and any end date printed near the table. Every snapshot gets its own BM25 passage index. Branch address cards and phone lines count as content in search, although they are short lines. A page that drops out of a later crawl is carried forward at lower authority.
- *Questions.* A question is parsed for its date (`as_of`, or a date written in the question), metric (APY, APR, dividend), term, amount, model year and intent: rate, hours, address, count, event, link, phone or yes/no. Member wording is mapped to the site's on the question side, for example a car named by make is an auto loan, buying a house is a mortgage, and a cashier's check is an official check. A reader for that intent then answers from the snapshot for that date:
  - rates come from rate facts: the row that matches the term and tier, on the highest-authority page;
  - hours come from the published schedules;
  - counts and events come from the newest dated release;
  - everything else goes to passage search.

  When the site does not state the answer, the reply is a refusal with `confident=False` and no numbers. Questions about the member's own balance or transactions are always declined.
- *Conflicts.* `conflicts()` reports three kinds of disagreement:
  - lower-authority pages against the rate sheet, for the same product, term and tier;
  - dated releases against each other, for counts such as branches;
  - dated releases against each other, for recognitions.

  Each entry is one fact. It lists the competing values, gives the current value as `resolution`, and says why. Different tiers, or a rate next to its own APY, are not conflicts.

**Optional model reader** (`llm.py`). With `GEMINI_API_KEY` set, the rules answer first, and their choices still decide the snapshot, the page authority and the evidence. A question then goes to `gemini-3.5-flash-lite` or stays with the rules, depending on the reader that answered:

- The model reads questions answered by passage search, rate tables, links, phone lookup, addresses and events, and rate questions the rules declined: about 90% of questions.
- The rules keep hours, counts, rate links, and rate checks such as "does any tier pay 0.35%?". For these the rules measured better, or the rate sheet should decide by design.

The model sees up to ten pages of evidence, about 4,000 tokens. Questions about awards, rankings or the latest news get six pages, because more pages there mostly add older releases. Pages the rules cited come first, then the matching rate rows, then the top-ranked passages. Each window keeps its table header, effective date and fine print, and each page is labelled with its type and its crawl and publication dates. The prompt states the rules for what is current, and adds that an amount belongs to the tier whose range contains it and that branches appear as address cards named by street or city.

A reply is kept only if every number in it appears in that evidence, the rules' draft or the question. Otherwise, and on any error or timeout (4 s per attempt, 6 s in all), the rules answer stands. The rules' sources are kept next to the model's citations. Three kinds of reply become refusals: one that says it cannot confirm, one that says the excerpts do not list the value, and one that gives no figure to a question asking for one, unless it says the thing is free.

## How "current" is decided

1. **Snapshot by date.** Without `as_of`, use the newest snapshot. With `as_of`, or a date written in the question, use the newest snapshot taken on or before that date.
2. **Page dates.** Every page has its own `fetched_at`, so a page inside a later snapshot can be an older copy. A page missing from a later crawl does not by itself void its facts: it stays, at lower authority.
3. **Authority for rates and fees.** The official rate sheet beats product pages. Product pages beat documents, documents beat promotion pages, and promotion pages beat calculators, news releases and blog posts.
4. **News-type facts.** For awards, counts and events, the most recently dated release wins.
5. **Expired offers.** A promotion whose end date is before the question's date is stale.
6. **Within a rate sheet.** When the same product appears twice, the row with the newest effective date wins.

## API keys and cost

- **None required.** Without a key the system is the rules alone, and `requirements.txt` installs nothing.
- **Optional: `GEMINI_API_KEY`** (paid Google Gemini API). It can be set in the environment, or in a `.env` file at the repository root, which git ignores.
  - It enables the model reader with `gemini-3.5-flash-lite`. `GEMINI_MODEL` overrides the model, and `TRUENOW_LLM=off` switches the reader off.
  - A model call sends about 4,200 tokens of evidence and costs about $0.002. That was measured as $2.82 for 1,457 calls, at $0.30 / $2.50 per million input / output tokens.
  - The model adds about one second per question. The p95 answer time was 1.36 s on the practice set and 1.07 s on the holdout.

## How it was measured

Every change was checked with the kit's scorer. Besides the practice set, the system was measured on independent question sets in the practice set's format. They were not written alongside this code, and they are not included in this repository. Items whose expected values do not appear in the snapshots were dropped, which left 1,686 questions, plus a separate holdout.

The model, the routing and every later change were chosen on these questions. At first only a stratified sample of 447 was used. After one untouched measurement on 7 November, the remaining 1,239 were used too.

A frozen holdout of 394 questions, set aside at the start, was never used to make a decision. It was measured twice: on the first version with the model, and on this final version.

| Question set | Rules only | Rules + model | p95 with model |
| --- | --- | --- | --- |
| Kit practice set (40; the rules were tuned on it) | 0.907 | 0.922 | 1.36 s |
| `bench/dev.jsonl` (57; written with the code) | 0.989 | 1.000 | 1.09 s |
| Independent sample (447; choices made here) | 0.689 | 0.874 | not timed |
| Independent rest (1,239; tuned on after a first measurement) | 0.621 | 0.846 | not timed |
| **Frozen holdout (394; never used for a decision)** | **0.643** | **0.810** | 1.07 s |

The sample and the rest were scored with the kit's scoring function by a parallel runner, so their answer times are not representative. The other rows come from `harness/run_eval.py`, which asks one question at a time.

Before any tuning on them, the first version with the model scored 0.592 (rules only) and 0.795 (with the model) on the 1,239, and 0.626 and 0.785 on the holdout. On the holdout, the final version gains 0.017 without a key and 0.025 with it. Those gains come from changes chosen on the other sets.

On the holdout, the model also changed these numbers:

- **Consistency** (paraphrase groups and repeated questions) is 0.95 with the model and 0.90 without it.
- **Stale-value hits** are 8 with the model and 5 without it (see Known gaps).
- **By category**, the model is far ahead on lists (0.80 against 0.29), current values (0.82 against 0.49) and tiers (0.95 against 0.73). It is behind on "as of" questions (0.79 against 0.84).

**Choosing the model.** Candidates were screened on 14 questions, then run one question at a time, the way the harness asks. This was done with the earlier six-page evidence pack:

| Model | Accuracy (same 211 questions, routing applied) | p95 | Same answer when asked again | Price in / out per 1M tokens |
| --- | --- | --- | --- | --- |
| `gemini-3.5-flash-lite` | **0.903** | **1.0 s** | 60 / 60 | $0.30 / $2.50 |
| `gemini-3.8-flash`, minimal thinking | 0.884 | 3.1 s | 28 / 30 | $0.75 / $3.75 |

With the same routing, the rules alone scored 0.705 on those 211 questions. Some models were not run in full:

- `gemini-3.6-flash` with default thinking took about 12 s per answer in the 14-question screen, which ran on the free tier;
- `gemini-3.1-flash-lite` and `gemini-3.6-flash` with minimal thinking were stopped early to stay within budget;
- the Pro models were not run;
- the 2.5 models are no longer offered to new API keys.

**Choosing what the model handles.** The table compares the rules and the model, reader by reader, on the 447-question sample (model replies include the source and refusal fixes above):

| Reader that answered | Questions | Rules | Model | Kept |
| --- | --- | --- | --- | --- |
| Rate table rows | 194 | 0.901 | 0.948 | model |
| Passage search | 120 | 0.384 | 0.755 | model |
| Rate question the rules declined | 45 | 0.286 | 0.876 | model |
| Counts (newest dated release) | 18 | 0.778 | 0.658 | rules |
| Hours | 17 | 0.681 | 0.606 | rules |
| Links | 17 | 0.597 | 0.706 | model |
| Phone | 11 | 0.480 | 0.623 | model |
| Hours questions the rules declined | 6 | 0.833 | 1.000 | model |
| Addresses, events, rate links | 15 | 1.000 | 0.877 | rules, then model for addresses and events |
| Rate passages the rules declined, rate status | 4 | 0.788 | 0.750 | rules |

Rate checks (a rate question that quotes a percentage) stay with the rules by design. The sample had none of them. The one practice question of this kind was answered wrongly by the model: it found 0.35% on a different product line.

Once branch cards reached the evidence, the routing was measured again on all 1,686 independent questions, one reader at a time:

- Addresses and events moved to the model: 2 more right, none lost.
- Counts stayed with the rules: the practice set lost a question when the model answered them.
- Hours stayed with the rules too: 8 gained, 7 lost.

## Changes tried and dropped

A change was kept only if it raised the score without losing questions elsewhere. These were measured on the 1,239 and dropped:

| Change | Gained | Lost |
| --- | --- | --- |
| A rate reply must name the term and variant asked (it rejected "regular, not jumbo" answers) | 4 | 16 |
| A reply to a declined rate question must state a rate | 5 | 10 |
| Rules-only passage answers must share half of the question's rarity-weighted words | 10 | 43 |
| Counts also read written-out numbers ("Thirty-two nonprofits") | 0 | 2 |

## Known gaps

- **Retrieval.** For about 8% of the independent questions that have an answer, the expected answer is not in the evidence at all; with the earlier six-page pack it was 11%. Most are details on pages the search ranks low, such as a branch phone number inside a news release.
- **"What changed" questions.** The model sees the evidence from one snapshot, so it cannot compare two dates. It scores 0 on the holdout's 8 such questions. The fix would be to keep them with the rules, or to give the model both snapshots.
- **Relative dates.** A month written without a year ("this past May") is not read as a date, so the latest values are used. The holdout's 4 relative-date questions score 0.25.
- **Extra values.** With ten pages of evidence, the model sometimes adds a second product's value, for example a refinance rate next to the purchase rate. The scorer counts that as a stale value. Of the 3 stale-value hits the final version added on the holdout, 2 come from this and 1 from a relative date.
- **Weakest categories with the model**, on the holdout: conflict resolution 0.58, stale-value traps 0.77, "as of" questions 0.79, and questions that should be refused 0.79. The last one was 0.88 in the earlier version. On the tuning sets, the drop came with the larger evidence pack: more evidence tempts the model to answer. Without a key, the weakest are lists 0.29, conflict resolution 0.47 and current values 0.49.
- **Found on the holdout, not tuned.** The "what changed", relative-date and extra-value gaps were found on the holdout, so they were not tuned there.
- **Conflicts.** Against eight independently labelled conflict lists, the mean F1 is 0.55 and the resolution accuracy 0.56. The 0.96 on our own list (`bench/conflicts_gold.json`) is circular.
- **Determinism.** Calls use temperature 0 and a fixed seed, and answers are cached per question within a run. Repeated questions gave the same answer 60 times out of 60. The provider can still change the model behind a name.
