"""Score the submission on several question sets with the kit's own scorer.

    python bench/run.py                      # public + dev sets
    python bench/run.py bench/dev.jsonl      # one set

Each set runs through harness/run_eval.py (results under .cache/bench/<set>/),
then a summary prints the score per category and every question that lost points.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SETS = [ROOT / "eval" / "public_eval.jsonl", ROOT / "bench" / "dev.jsonl"]
CONFLICTS_GOLD = ROOT / "bench" / "conflicts_gold.json"


def run(eval_path: Path) -> dict:
    out = ROOT / ".cache" / "bench" / eval_path.stem
    command = [sys.executable, str(ROOT / "harness" / "run_eval.py"), "--submission", str(ROOT / "submission"),
               "--snapshots", str(ROOT / "snapshots"), "--eval", str(eval_path), "--out", str(out)]
    if CONFLICTS_GOLD.exists():
        command += ["--conflicts-gold", str(CONFLICTS_GOLD)]
    subprocess.run(command, check=True, capture_output=True, text=True)
    report = json.loads((out / "report.json").read_text())
    report["misses"] = [json.loads(l) for l in (out / "per_question.jsonl").read_text().splitlines()
                        if l.strip() and json.loads(l)["score"] < 0.999]
    return report


def main(paths: list) -> None:
    for path in paths:
        report = run(Path(path))
        speed, cons = report["speed"], report["consistency"]
        line = (f"\n== {Path(path).name}: qa {report['qa_score']:.3f} | final {report['final_score']:.3f} | "
                f"p95 {speed['p95_s']}s | consistency {cons['score']} | traps hit {report['stale_trap_hits']}")
        if "conflicts" in report:
            c = report["conflicts"]
            line += f" | conflicts f1 {c['f1']} ({c['matched']}/{c['submitted']} of {c['gold']} gold)"
        print(line)
        for category, value in report["by_category"].items():
            print(f"   {category:22s} {value['score']:.2f} (n={value['n']})")
        for miss in report["misses"]:
            text = miss["answer"]["text"].replace("\n", " ")[:150]
            print(f"   - {miss['id']} {miss['score']:.2f} missed={miss['missed']} traps={miss['trap_hits']} | {text}")


if __name__ == "__main__":
    main(sys.argv[1:] or [str(p) for p in DEFAULT_SETS])
