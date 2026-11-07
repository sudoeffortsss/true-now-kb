# Notes

## Approach

The system answers from the five snapshots only. A rules core decides what is current and gathers the evidence. An optional model reader (Gemini, used only when a key is set) then writes the answer from that evidence, for the question types where it measured better than the rules. Everything is in `submission/truenow/` and uses the Python standard library only.

**Rules core.**

- *Ingest.* Each page is cleaned: site widgets, footnote markers and the "Handpicked for You" teasers are removed. The URL gives the page type, in authority order: rate sheet, product page, document, promotion page, calculator, news release, blog article. Markdown tables become rate facts, each with its product, section, term, balance tier, model years, every APY, APR and dividend cell, the "effective as of" date, and any end date printed near the table. Every snapshot gets its own BM25 passage index. A page that drops out of a later crawl is carried forward at lower authority.
- *Questions.* A question is parsed for its date (`as_of`, or a date written in the question), metric (APY, APR, dividend), term, amount, model year and intent: rate, hours, address, count, event, link, phone or yes/no. A reader for that intent then answers from the snapshot for that date:
  - rates come from rate facts: the row that matches the term and tier, on the highest-authority page;
  - hours come from the published schedules;
  - counts and events come from the newest dated release;
  - everything else goes to passage search.

  When the site does not state the answer, the reply is a refusal with `confident=False` and no numbers.
- *Conflicts.* `conflicts()` reports three kinds of disagreement:
  - lower-authority pages against the rate sheet, for the same product, term and tier;
  - dated releases against each other, for counts such as branches;
  - dated releases against each other, for recognitions.

  Each entry is one fact. It lists the competing values, gives the current value as `resolution`, and says why. Different tiers, or a rate next to its own APY, are not conflicts.

**Optional model reader** (`llm.py`). With `GEMINI_API_KEY` set, the rules answer first. The rules' choices still decide the snapshot, the page authority and the evidence. Questions then go to `gemini-3.5-flash-lite` or stay with the rules, by the reader that answered:

- The model reads questions answered by passage search, rate tables, links or phone lookup, and rate questions the rules declined.
- The rules keep hours, counts, addresses, events, rate links, and rate checks such as "does any tier pay 0.35%?". For these, the rules measured better, or the rate sheet should decide by design.

The model sees up to six pages of evidence, about 3,000 tokens. Pages the rules cited come first, then the matching rate rows, then the top-ranked passages. Each window keeps its table header, effective date and fine print, and each page is labelled with its type and its crawl and publication dates. The model's reply is kept only if every number in it appears in that evidence, the rules' draft or the question. Otherwise, and on any error or timeout (4 s per attempt, 6 s in all), the rules answer stands. The rules' sources are kept next to the model's citations. A reply that says it cannot confirm becomes a refusal.

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
  - About 87% of questions go to the model, at about 3,700 input and 70 output tokens each. That is roughly $0.0013 per model call at $0.30 / $2.50 per million tokens.
  - The model adds about one second per question. The p95 answer time was 1.2 s on the practice set.

## How it was measured

Every change was checked with the kit's scorer. Besides the practice set, the system was measured on independent question sets in the practice set's format. They were not written alongside this code, and they are not included in this repository. Items whose expected values do not appear in the snapshots were dropped, which left about 1,700 questions.

The model and the routing were chosen on a stratified sample of 447 of these questions. The remaining 1,239, and a frozen holdout of 394 questions set aside at the start, were measured only once, after those choices were made.

| Question set | Rules only | Rules + model | p95 with model |
| --- | --- | --- | --- |
| Kit practice set (40; the rules were tuned on it) | 0.907 | 0.922 | 1.19 s |
| `bench/dev.jsonl` (57; written with the code) | 0.989 | 1.000 | 0.98 s |
| Independent sample (447; model and routing chosen here) | 0.669 | 0.854 | 0.97 s |
| Independent, rest of the pool (1,239; untouched) | 0.592 | 0.795 | 0.97 s |
| Frozen holdout (394; untouched) | 0.626 | 0.785 | 0.98 s |

On the two untouched sets, the model also helped in these ways:

- The consistency the harness measures (paraphrase groups and repeated questions) rose from 0.88 to 0.94 on the 1,239, and from 0.90 to 0.94 on the holdout.
- Stale-value hits fell from 36 to 19 on the 1,239. The holdout had 5 both times.
- Only one category scored lower: "what changed" questions in the holdout, 8 questions, which fell from 0.31 to 0.00 (see Known gaps).

**Choosing the model.** Candidates were screened on 14 questions, then run one question at a time, the way the harness asks:

| Model | Accuracy (same 211 questions, routing applied) | p95 | Same answer when asked again | Price in / out per 1M tokens |
| --- | --- | --- | --- | --- |
| `gemini-3.5-flash-lite` | **0.903** | **1.0 s** | 60 / 60 | $0.30 / $2.50 |
| `gemini-3.8-flash`, minimal thinking | 0.884 | 3.1 s | 28 / 30 | $0.75 / $3.75 |

With the same routing, the rules alone scored 0.705 on those 211 questions. Some models were not run in full:

- `gemini-3.6-flash` with default thinking took about 12 s per answer in the 14-question screen, which ran on the free tier;
- `gemini-3.1-flash-lite` and `gemini-3.6-flash` with minimal thinking were stopped early to stay within budget;
- the Pro models were not run;
- the 2.5 models are no longer offered to new API keys.

**Choosing what the model handles.** Here are the rules and the model, reader by reader, on the 447-question sample (model replies include the source and refusal fixes above):

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
| Addresses, events, rate links | 15 | 1.000 | 0.877 | rules |
| Rate passages the rules declined, rate status | 4 | 0.788 | 0.750 | rules |

Rate checks (a rate question that quotes a percentage) stay with the rules by design. The sample had none of them. The one practice question of this kind was answered wrongly by the model: it found 0.35% on a different product line.

## Known gaps

- **Retrieval.** In the sample, the expected answer appears in the evidence for about 90% of the questions that have an answer. The model cannot fix the other 10%, mostly branch details, links and long lists.
- **Weakest categories.** With the model, on the untouched sets:
  - conflict resolution: 0.45 on the 1,239, 0.47 on the holdout;
  - branch details: 0.62 on the 1,239;
  - stale-value traps: 0.72 and 0.74.

  Without a key, the weakest are lists (0.36 and 0.23), open facts (0.43) and current values (0.51 and 0.47).
- **"What changed" questions.** The model sees the evidence from one snapshot, so it cannot compare two dates. In the holdout it scored 0 on these 8 questions, where the rules got 0.31. The fix would be to keep them with the rules, or to give the model both snapshots. This was found on the holdout, so it was not tuned there.
- **Model refusals.** The model sometimes declines a question the rules had answered correctly. Keeping its refusal measured better overall, but it costs some questions.
- **Conflicts.** Against eight independently labelled conflict lists, the mean F1 is 0.55 and the resolution accuracy 0.56. The 0.96 on our own list (`bench/conflicts_gold.json`) is circular.
- **Determinism.** Calls use temperature 0 and a fixed seed, and answers are cached per question within a run. Repeated questions gave the same answer 60 times out of 60. The provider can still change the model behind a name.
