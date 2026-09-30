"""Compare Jev judgments with gold verdicts (and optionally Opus results).

Every sample set is reported on its own. The design set is shown only as a
diagnostic; performance claims come from sets that were not used to tune the
questions.

    python experiments/jev/jev_analyze.py --runs results/jev \
        --problems examples/ex-002 examples/ex-003 \
        --answers examples/ex-002/answers examples/ex-003/answers \
        --gold experiments/jev/gold \
        --requirements experiments/jev/requirements \
        --opus results/v2-ex002-r01 results/v2-ex003-r01 \
        --criteria experiments/jev/criteria.yaml --out results/jev-report
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jev_common import (  # noqa: E402
    VERDICTS,
    body_sha256,
    discover_cases,
    expected_items,
    infer_sample_set,
    item_predictions,
    load_requirements,
    sha256_file,
)

from nonsul_review.io import read_json, read_yaml  # noqa: E402

DEFAULT_TAUS = (0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99)


def load_gold(dirs: list[Path]) -> dict[tuple[str, str], str]:
    gold: dict[tuple[str, str], str] = {}
    for directory in dirs:
        for path in sorted(directory.glob("*.yaml")):
            data = read_yaml(path)
            for item in data.get("items", []):
                if item.get("unresolved") or item.get("verdict") not in VERDICTS:
                    continue
                gold[(data["answer_id"], item["rubric_id"])] = item["verdict"]
    return gold


def load_opus(dirs: list[Path]) -> dict[tuple[str, str, str], tuple[str, str | None]]:
    """(answer_id, rubric_id, mode) -> (verdict, answer_body_sha256) from nonsul-review results."""
    out: dict[tuple[str, str, str], tuple[str, str | None]] = {}
    for directory in dirs:
        for path in sorted(directory.glob("*.json")):
            if path.name.startswith("batch.") or ".raw." in path.name:
                continue
            data = read_json(path)
            if not isinstance(data, dict) or "items" not in data:
                continue
            body_hash = (data.get("meta") or {}).get("answer_body_sha256")
            for item in data["items"]:
                if item.get("verdict") in VERDICTS:
                    out[(data["answer_id"], item["rubric_id"], data["mode"])] = (
                        item["verdict"],
                        body_hash,
                    )
    return out


def block_of(meta: dict[str, Any]) -> tuple[str, str, str]:
    return (meta["variant"], meta["lang"], str(meta.get("model")))


def load_runs(
    runs: Path, requirements: dict[str, Any], cases_by_id: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read every record; a failed attempt is superseded by a later success of the same run."""
    audit: dict[str, Any] = {"costs": [], "stale": [], "requests": Counter(), "issues": []}
    chosen: dict[Path, tuple[Path, dict[str, Any]]] = {}
    for path in sorted(runs.rglob("*.r[0-9]*.json")):
        record = read_json(path)
        meta = record["meta"]
        usage = meta.get("usage") or {}
        audit["costs"].append(
            {
                "block": block_of(meta),
                "cost": meta.get("cost"),
                "cost_status": meta.get("cost_status", "unknown"),
                "input_tokens": usage.get("input_tokens", usage.get("inputTokens")),
            }
        )
        run_id = path.with_name(path.name.replace(".failed.json", ".json"))
        if run_id in chosen and path.name.endswith(".failed.json"):
            continue
        chosen[run_id] = (path, record)

    rows: list[dict[str, Any]] = []
    for path, record in chosen.values():
        meta = record["meta"]
        block = block_of(meta)
        case = cases_by_id.get(meta["answer_id"])
        if case is None:
            audit["stale"].append(f"{path}: answer not among the supplied inputs")
            continue
        if meta.get("answer_sha256") != sha256_file(case.answer_path) or meta.get(
            "problem_sha256"
        ) != sha256_file(case.problem_path):
            audit["stale"].append(f"{path}: problem or answer changed since this run")
            continue
        reqs = requirements.get(meta["problem_id"]) if meta["variant"] == "C" else None
        if meta["variant"] == "C":
            if reqs is None:
                audit["stale"].append(f"{path}: no requirements file for {meta['problem_id']}")
                continue
            if meta.get("requirements_sha256") != reqs["__sha256__"]:
                audit["stale"].append(f"{path}: requirements changed since this run")
                continue
            reqs = {k: v for k, v in reqs.items() if k != "__sha256__"}
        expected = expected_items(record["request"])
        failed = meta.get("http_status") != 200 or path.name.endswith(".failed.json")
        if failed:
            preds, issues = {}, {rid: "request failed" for rid in expected}
            audit["requests"][(block, "failed")] += 1
        else:
            preds, issues = item_predictions(meta["variant"], record["response"], expected, reqs)
            audit["requests"][(block, "complete" if not issues else "incomplete")] += 1
        audit["issues"] += [f"{path.name} {rid}: {why}" for rid, why in issues.items()]
        for rid in expected:
            pred = preds.get(rid, {"verdict": None, "confidence": 0.0, "probabilities": {}})
            rows.append(
                {
                    "block": block,
                    "answer_id": meta["answer_id"],
                    "rubric_id": rid,
                    "repeat": meta["repeat"],
                    "body_sha256": body_sha256(case.answer),
                    **pred,
                }
            )
    return rows, audit


def spend(costs: list[dict[str, Any]]) -> dict[str, Any]:
    charged = [c["cost"] for c in costs if isinstance(c.get("cost"), (int, float))]
    tokens = [c["input_tokens"] for c in costs if isinstance(c.get("input_tokens"), (int, float))]
    return {
        "records": len(costs),
        "cost_status": dict(Counter(c["cost_status"] for c in costs)),
        "reported_cost_total": round(sum(charged), 6) if charged else None,
        "input_tokens_total": sum(tokens) if tokens else None,
    }


def load_sets(manifest: Path | None, cases) -> dict[str, str]:
    sets = {case.answer.id: infer_sample_set(case.answer) for case in cases}
    if manifest:
        data = read_yaml(manifest) or {}
        for set_name, answer_ids in data.items():
            for aid in answer_ids or []:
                sets[aid] = set_name
    return sets


def summarize(items: list[dict[str, Any]], taus: tuple[float, ...]) -> dict[str, Any]:
    """Denominators include undecided items (missing, invalid or failed responses)."""
    scored = [r for r in items if r.get("gold")]
    n = len(scored)
    decided = [r for r in scored if r["verdict"] is not None]
    confusion = Counter((r["gold"], r["verdict"] or "undecided") for r in scored)
    curve = []
    for tau in taus:
        covered = [r for r in decided if r["confidence"] >= tau]
        correct = sum(r["gold"] == r["verdict"] for r in covered)
        curve.append(
            {
                "tau": tau,
                "coverage": round(len(covered) / n, 4) if n else None,
                "covered": len(covered),
                "covered_accuracy": round(correct / len(covered), 4) if covered else None,
                "high_confidence_false_met": sum(
                    r["verdict"] == "met" and r["gold"] != "met" for r in covered
                ),
            }
        )
    gold_met = [r for r in decided if r["gold"] == "met"]
    return {
        "items": n,
        "undecided": n - len(decided),
        "accuracy": round(sum(r["gold"] == r["verdict"] for r in scored) / n, 4) if n else None,
        "false_met": sum(r["verdict"] == "met" and r["gold"] != "met" for r in scored),
        "wrongly_deducted_share": (
            round(sum(r["verdict"] != "met" for r in gold_met) / len(gold_met), 4)
            if gold_met
            else None
        ),
        "confusion": {f"{g}->{p}": c for (g, p), c in sorted(confusion.items())},
        "curve": curve,
    }


def stability(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[(r["answer_id"], r["rubric_id"])].append(r)
    repeated = [g for g in groups.values() if len(g) > 1]
    if not repeated:
        return {"repeated_items": 0}
    same = sum(len({r["verdict"] for r in g}) == 1 for g in repeated)
    spread = [max(r["confidence"] for r in g) - min(r["confidence"] for r in g) for g in repeated]
    return {
        "repeated_items": len(repeated),
        "identical_verdict_share": round(same / len(repeated), 4),
        "mean_confidence_range": round(mean(spread), 4),
    }


def versus_opus(first: list[dict[str, Any]], opus: dict, mode: str) -> dict[str, Any]:
    """Compare only when the Opus result was produced from the same answer body."""
    pairs, mismatched = [], 0
    for r in first:
        found = opus.get((r["answer_id"], r["rubric_id"], mode))
        if not found or r["verdict"] is None:
            continue
        verdict, body_hash = found
        if body_hash != r["body_sha256"]:
            mismatched += 1
            continue
        pairs.append((r, verdict))
    if not pairs:
        return {"compared": 0, "skipped_answer_mismatch": mismatched}
    disagree = [(r, o) for r, o in pairs if r["verdict"] != o]
    judged = [(r, o) for r, o in disagree if r.get("gold")]
    return {
        "compared": len(pairs),
        "skipped_answer_mismatch": mismatched,
        "agreement": round(1 - len(disagree) / len(pairs), 4),
        "disagreements": len(disagree),
        "jev_right": sum(r["verdict"] == r["gold"] for r, _ in judged),
        "opus_right": sum(o == r["gold"] for r, o in judged),
        "neither_right": sum(r["verdict"] != r["gold"] and o != r["gold"] for r, o in judged),
    }


def evaluate_criteria(report: dict[str, Any], criteria: dict[str, Any]) -> list[str]:
    tau = criteria["tau"]
    if tau not in DEFAULT_TAUS:
        raise ValueError(f"criteria tau must be one of {DEFAULT_TAUS}")
    lines = []
    for key, block in report.items():
        sets = block["sets"]

        def at(set_name: str, _sets: dict[str, Any] = sets) -> dict[str, Any] | None:
            summary = _sets.get(set_name)
            if not summary or not summary["items"]:
                return None
            return next(p for p in summary["curve"] if p["tau"] == tau) | {"summary": summary}

        checks = []
        risk = at(criteria.get("risk_set", "risk"))
        checks.append(
            (
                f"risk: high-confidence false met <= {criteria['max_high_conf_false_met_on_risk']}",
                None
                if risk is None
                else risk["high_confidence_false_met"]
                <= criteria["max_high_conf_false_met_on_risk"],
            )
        )
        alt = sets.get(criteria.get("alternative_set", "alternative"))
        checks.append(
            (
                f"alternative: wrongly deducted <= {criteria['max_alternative_false_deduction']}",
                None
                if not alt or alt["wrongly_deducted_share"] is None
                else alt["wrongly_deducted_share"] <= criteria["max_alternative_false_deduction"],
            )
        )
        normal = at(criteria.get("normal_set", "normal"))
        checks.append(
            (
                f"normal: coverage >= {criteria['min_normal_coverage']}",
                None if normal is None else normal["coverage"] >= criteria["min_normal_coverage"],
            )
        )
        checks.append(
            (
                f"normal: covered accuracy >= {criteria['min_normal_covered_accuracy']}",
                None
                if normal is None or normal["covered_accuracy"] is None
                else normal["covered_accuracy"] >= criteria["min_normal_covered_accuracy"],
            )
        )
        for label, ok in checks:
            status = "NOT EVALUATED (no data)" if ok is None else ("PASS" if ok else "FAIL")
            lines.append(f"{key} @ tau={tau}: {label} -> {status}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--problems", nargs="+", type=Path, required=True)
    parser.add_argument("--answers", nargs="+", type=Path, required=True)
    parser.add_argument("--gold", nargs="+", type=Path, required=True)
    parser.add_argument("--requirements", nargs="*", type=Path, default=[])
    parser.add_argument("--opus", nargs="*", type=Path, default=[])
    parser.add_argument("--opus-mode", choices=("rubric", "free"), default="rubric")
    parser.add_argument("--sets", type=Path, help="YAML: set name -> list of answer IDs")
    parser.add_argument("--criteria", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    cases = discover_cases(args.problems, args.answers)
    cases_by_id = {c.answer.id: c for c in cases}
    requirements: dict[str, Any] = {}
    for directory in args.requirements:
        for pid, problem in {c.problem.id: c.problem for c in cases}.items():
            path = directory / f"{pid}.yaml"
            if path.is_file():
                requirements[pid] = {
                    **load_requirements(path, problem),
                    "__sha256__": sha256_file(path),
                }

    gold = load_gold(args.gold)
    opus = load_opus(args.opus)
    sets = load_sets(args.sets, cases)
    rows, audit = load_runs(args.runs, requirements, cases_by_id)
    if not rows:
        print("no usable Jev results", *audit["stale"], sep="\n", file=sys.stderr)
        return 1
    for r in rows:
        r["gold"] = gold.get((r["answer_id"], r["rubric_id"]))
        r["set"] = sets.get(r["answer_id"], "unknown")

    report: dict[str, Any] = {}
    for block in sorted({r["block"] for r in rows}):
        block_rows = [r for r in rows if r["block"] == block]
        first = [r for r in block_rows if r["repeat"] == 0]
        requests = {k[1]: v for k, v in audit["requests"].items() if k[0] == block}
        report["variant-{}/{}/{}".format(*block)] = {
            "requests": requests,
            "sets": {
                s: summarize([r for r in first if r["set"] == s], DEFAULT_TAUS)
                for s in sorted({r["set"] for r in first})
            },
            "stability": stability([r for r in block_rows if r["verdict"] is not None]),
            "versus_opus": versus_opus(first, opus, args.opus_mode),
            "spend": spend([c for c in audit["costs"] if c["block"] == block]),
        }

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "report.json").write_text(
        json.dumps(
            {"report": report, "stale": audit["stale"], "issues": audit["issues"]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    with (args.out / "items.csv").open("w", newline="", encoding="utf-8") as fh:
        fields = ["block", "set", "answer_id", "rubric_id", "repeat", "gold", "verdict"]
        fields += ["confidence", "probabilities"]
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for r in rows:
            writer.writerow(
                {
                    **{f: r.get(f) for f in fields},
                    "block": "/".join(r["block"]),
                    "probabilities": json.dumps(r["probabilities"]),
                }
            )

    lines = ["# Jev pilot report", ""]
    lines.append("Sets are reported separately and never merged. `design` is diagnostic only.")
    lines.append(
        "Confidence in variants A/B is the provider statistic; in C it is "
        "min(max(p, 1-p)) over requirements. Do not read equal values as equal accuracy."
    )
    for key, block in report.items():
        lines += ["", f"## {key}", "", f"- requests: {block['requests']}", ""]
        for set_name, s in block["sets"].items():
            lines.append(
                f"- **{set_name}**: items {s['items']}, undecided {s['undecided']}, "
                f"accuracy {s['accuracy']}, false met {s['false_met']}, "
                f"wrongly deducted (gold met) {s['wrongly_deducted_share']}"
            )
            lines.append("")
            lines.append("  | tau | coverage | covered accuracy | high-conf false met |")
            lines.append("  | --- | --- | --- | --- |")
            for p in s["curve"]:
                lines.append(
                    f"  | {p['tau']} | {p['coverage']} | {p['covered_accuracy']} "
                    f"| {p['high_confidence_false_met']} |"
                )
        lines.append(f"- stability: {block['stability']}")
        lines.append(f"- versus Opus ({args.opus_mode}): {block['versus_opus']}")
        lines.append(f"- spend: {block['spend']}")
    if args.criteria:
        lines += ["", "## Preregistered criteria", ""]
        lines += [f"- {line}" for line in evaluate_criteria(report, read_yaml(args.criteria))]
    for title, entries in (
        ("Excluded (stale inputs)", audit["stale"]),
        ("Response issues", audit["issues"]),
    ):
        if entries:
            lines += ["", f"## {title}", ""] + [f"- {e}" for e in entries]
    (args.out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {args.out / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
