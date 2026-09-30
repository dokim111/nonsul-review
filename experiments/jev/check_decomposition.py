"""Check that a requirement decomposition can express the human gold.

Before a test run, a person writes yes/no for each requirement of the boundary
items and the intended gold verdict. This script applies variant C's
aggregation rule to those human answers. A mismatch means the requirements
(or their base/full roles) cannot represent the agreed grading policy, which
must be fixed before the model is run, never by changing a defensible gold.

    python experiments/jev/check_decomposition.py \
        --problems data/private/jev-test/problems \
        --requirements experiments/jev/requirements-v3 data/private/jev/requirements-v3 \
        --human data/private/jev-test/human-requirements.yaml

Human file format:

    - answer_id: ex-003-t04
      problem_id: ex-003
      items:
        R2:
          gold: partial
          requirements: {R2.inequality: yes, R2.structure: no}
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jev_common import (  # noqa: E402
    VERDICTS,
    aggregate_requirements,
    discover_problems,
    load_requirements,
)

from nonsul_review.io import read_yaml  # noqa: E402


def check(problem_roots, requirement_dirs, human_path) -> list[str]:
    problems = discover_problems(problem_roots)
    reqs = {}
    for pid, (problem, _) in problems.items():
        for directory in requirement_dirs:
            path = directory / f"{pid}.yaml"
            if path.is_file():
                reqs[pid] = load_requirements(path, problem)
    entries = read_yaml(human_path) or []
    problems_found: list[str] = []
    for entry in entries:
        aid, pid = entry["answer_id"], entry["problem_id"]
        if pid not in reqs:
            problems_found.append(f"{aid}: no requirements for {pid}")
            continue
        for rid, item in entry["items"].items():
            gold = item["gold"]
            if gold not in VERDICTS:
                problems_found.append(f"{aid} {rid}: gold {gold!r} is not a verdict")
                continue
            spec = reqs[pid][rid]
            human = item["requirements"]
            expected = {r["id"] for r in spec}
            if set(human) != expected:
                problems_found.append(
                    f"{aid} {rid}: human answers {sorted(human)} != requirements {sorted(expected)}"
                )
                continue
            yes = {k: 1.0 if v is True else 0.0 for k, v in human.items()}
            verdict, _ = aggregate_requirements(spec, yes)
            if verdict != gold:
                problems_found.append(
                    f"{aid} {rid}: human requirements aggregate to {verdict}, gold is {gold}"
                )
    return problems_found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--problems", nargs="+", type=Path, required=True)
    parser.add_argument("--requirements", nargs="+", type=Path, required=True)
    parser.add_argument("--human", type=Path, required=True)
    args = parser.parse_args(argv)
    issues = check(args.problems, args.requirements, args.human)
    total = sum(len(e["items"]) for e in (read_yaml(args.human) or []))
    if issues:
        print(f"{len(issues)} of {total} checked items do not match:", *issues, sep="\n- ")
        return 1
    print(f"all {total} checked items aggregate to their gold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
