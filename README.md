# true-now-kb

A knowledge system that answers credit-union members' questions from five snapshots of the credit union's website, taken between May and September 2026. Like a careful human expert, it:

- gives the value that is true now, or on a given date;
- picks the right tier and cites a source;
- says "I don't know" when the site does not say;
- lists the places where the site contradicts itself.

The task is described in [ASSIGNMENT.md](ASSIGNMENT.md), the participant kit's README. How the system works, how it decides what is current, and how it was measured are in [NOTES.md](NOTES.md).

## Run it

Python 3.11. The system uses the standard library only, so the install step installs nothing.

```bash
pip install -r requirements.txt
python harness/run_eval.py --submission submission --eval eval/public_eval.jsonl
```

Unit tests need no snapshots, network or key: `python -m unittest discover -s tests`.

The website snapshots are not in this repository. Put the kit's five snapshot zips in `snapshots_zip/`; the first run unpacks them into `snapshots/`.

Without an API key the system is rules only. With `GEMINI_API_KEY` set (a paid Gemini API key, in the environment or in a `.env` file at the repository root), a model reader writes the answers for the question types where it measured better. `TRUENOW_LLM=off` switches it off again. See NOTES.md for what it costs and when it is used.

## Layout

| Path | Contents |
| --- | --- |
| `submission/solution.py` | Entry point the harness loads: `build()` |
| `submission/truenow/` | The system: page cleaning and types, tables and rate facts, search, question analysis, readers, conflicts, the optional model reader |
| `bench/` | Our own question set (`dev.jsonl`), conflict list and the scripts that run and check them |
| `tests/` | Unit tests, including an end-to-end run on the kit's toy site |
| `harness/`, `eval/`, `starter/`, `snapshots/README.md` | The participant kit, unchanged |
