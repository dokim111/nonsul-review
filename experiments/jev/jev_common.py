"""Shared pieces of the Jev pilot: inputs, question designs, and response parsing.

This experiment lives outside the ``nonsul_review`` package on purpose. It
reads the same problem and answer files, but it never changes the engine, its
prompts, or the rubric-versus-free comparison.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nonsul_review.io import load_answer, load_problem, read_yaml
from nonsul_review.schema import Answer, Problem

VERDICTS = ("met", "partial", "not_met")
VARIANTS = ("A", "B", "C")
PROBABILITY_TOLERANCE = 0.02
LANGS = ("ko", "en")

# --------------------------------------------------------------------------- inputs


@dataclass(frozen=True)
class Case:
    problem: Problem
    answer: Answer
    problem_path: Path
    answer_path: Path


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def discover_problems(roots: list[Path]) -> dict[str, tuple[Problem, Path]]:
    problems: dict[str, tuple[Problem, Path]] = {}
    for root in roots:
        paths = [root] if root.is_file() else sorted(root.rglob("problem.yaml"))
        for path in paths:
            problem = load_problem(path)
            if problem.id in problems:
                raise ValueError(f"duplicate problem id {problem.id}: {path}")
            problems[problem.id] = (problem, path)
    return problems


def discover_cases(problem_roots: list[Path], answer_roots: list[Path]) -> list[Case]:
    problems = discover_problems(problem_roots)
    cases: list[Case] = []
    seen: set[str] = set()
    for root in answer_roots:
        paths = [root] if root.is_file() else sorted(root.glob("*.md"))
        for path in paths:
            if path.name.lower() == "readme.md":
                continue
            answer = load_answer(path)
            if answer.problem not in problems:
                continue
            if answer.id in seen:
                raise ValueError(f"duplicate answer id {answer.id}: {path}")
            seen.add(answer.id)
            problem, problem_path = problems[answer.problem]
            cases.append(Case(problem, answer, problem_path, path))
    return cases


def build_state(problem: Problem, answer: Answer) -> dict[str, Any]:
    """The same source information the Opus procedures receive."""
    return {
        "problem_title": problem.title,
        "problem": problem.prompt,
        "model_answer": problem.model_answer,
        "accepted_alternatives": problem.alternatives or "",
        "rubric": [
            {
                "id": item.id,
                "step": item.step,
                "criteria": item.criteria,
                "partial_condition": item.partial or "",
            }
            for item in problem.rubric
        ],
        "student_answer": answer.body,
    }


# --------------------------------------------------------------------------- questions

_TEXT = {
    "ko": {
        "item": (
            "state.student_answer가 채점 기준 {rid}({step})를 충족하는지 판정하라. "
            "state.accepted_alternatives와 수학적으로 타당한 다른 풀이도 인정한다. "
            "결론이나 최종값이 맞아도 기준이 요구하는 근거가 빠졌으면 충족이 아니다. "
            "답안에 쓰이지 않은 논증을 채워 넣어 판단하지 마라.\n"
            "기준: {criteria}\n부분 인정 조건: {partial}"
        ),
        "none": "명시되지 않음",
        "met": "기준이 요구하는 내용과 근거를 모두 갖추었다",
        "partial": "부분 인정 조건에 해당하거나, 기준의 일부만 충족했다",
        "not_met": "기준을 충족하지 못했다",
        "req": (
            "state.student_answer만을 근거로 답하라. 답안에 명시적으로 쓰인 논증만 인정하고, "
            "쓰이지 않은 단계를 추론해 채워 넣지 마라. 질문: {question}"
        ),
    },
    "en": {
        "item": (
            "Decide whether state.student_answer satisfies rubric item {rid} ({step}). "
            "Accept state.accepted_alternatives and other mathematically valid solutions. "
            "A correct conclusion or final value does not satisfy the item when the "
            "required justification is missing. Do not fill in reasoning the answer "
            "does not contain.\nCriteria: {criteria}\nPartial condition: {partial}"
        ),
        "none": "not specified",
        "met": "Contains everything the criteria require, including the justification",
        "partial": "Matches the partial condition, or satisfies only part of the criteria",
        "not_met": "Does not satisfy the criteria",
        "req": (
            "Answer only from state.student_answer. Credit only reasoning that is "
            "explicitly written; do not infer missing steps. Question: {question}"
        ),
    },
}


def _item_text(problem: Problem, rid: str, lang: str) -> str:
    item = next(r for r in problem.rubric if r.id == rid)
    text = _TEXT[lang]
    return text["item"].format(
        rid=item.id, step=item.step, criteria=item.criteria, partial=item.partial or text["none"]
    )


def questions_variant_a(problem: Problem, lang: str) -> dict[str, Any]:
    """One Choice per rubric item over the three verdicts."""
    text = _TEXT[lang]
    return {
        item.id: {
            "type": "choice",
            "instructions": _item_text(problem, item.id, lang),
            "criteria": {v: text[v] for v in VERDICTS},
        }
        for item in problem.rubric
    }


def questions_variant_b(problem: Problem, lang: str) -> dict[str, Any]:
    """One Score per rubric item; levels ordered not_met < partial < met."""
    text = _TEXT[lang]
    return {
        item.id: {
            "type": "score",
            "instructions": _item_text(problem, item.id, lang),
            "criteria": [f"{v}: {text[v]}" for v in ("not_met", "partial", "met")],
        }
        for item in problem.rubric
    }


def load_requirements(path: Path, problem: Problem) -> dict[str, list[dict[str, str]]]:
    """Human-authored decomposition of rubric items into yes/no requirements."""
    data = read_yaml(path)
    if not isinstance(data, dict) or data.get("problem_id") != problem.id:
        raise ValueError(f"{path}: problem_id must be {problem.id}")
    rubric = data.get("rubric")
    known = {item.id for item in problem.rubric}
    if not isinstance(rubric, dict) or set(rubric) != known:
        raise ValueError(f"{path}: rubric must list exactly {sorted(known)}")
    seen: set[str] = set()
    for rid, reqs in rubric.items():
        if not isinstance(reqs, list) or not reqs:
            raise ValueError(f"{path}: {rid} needs at least one requirement")
        if not any(req.get("role") == "base" for req in reqs):
            raise ValueError(f"{path}: {rid} needs at least one base requirement")
        for req in reqs:
            if set(req) != {"id", "role", "question"} or req["role"] not in ("base", "full"):
                raise ValueError(f"{path}: {rid} requirement needs id, role(base|full), question")
            if req["id"] in seen or "." not in req["id"] or "__" in req["id"]:
                raise ValueError(f"{path}: bad or duplicate requirement id {req['id']}")
            seen.add(req["id"])
    return rubric


def questions_variant_c(
    problem: Problem, lang: str, requirements: dict[str, list[dict[str, str]]]
) -> dict[str, Any]:
    """One Noul per requirement; question keys are ``<rubric>__<requirement>``."""
    text = _TEXT[lang]
    questions: dict[str, Any] = {}
    for rid, reqs in requirements.items():
        for req in reqs:
            key = f"{rid}__{req['id']}"
            questions[key] = {
                "type": "noul",
                "instructions": text["req"].format(question=req["question"]),
            }
    return questions


def build_request(
    case: Case,
    variant: str,
    lang: str,
    model: str,
    requirements: dict[str, list[dict[str, str]]] | None = None,
) -> dict[str, Any]:
    if variant == "A":
        questions = questions_variant_a(case.problem, lang)
    elif variant == "B":
        questions = questions_variant_b(case.problem, lang)
    elif variant == "C":
        if requirements is None:
            raise ValueError(f"variant C needs a requirements file for {case.problem.id}")
        questions = questions_variant_c(case.problem, lang, requirements)
    else:
        raise ValueError(f"unknown variant {variant}")
    return {"model": model, "state": build_state(case.problem, case.answer), "questions": questions}


def request_digest(request: dict[str, Any]) -> str:
    blob = json.dumps(request, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


# --------------------------------------------------------------------------- responses


class ResponseShapeError(ValueError):
    """The response does not have a shape this pilot knows how to read."""


def answers_of(response: Any) -> dict[str, Any]:
    if isinstance(response, dict):
        for key in ("answers", "results"):
            if isinstance(response.get(key), dict):
                return response[key]
    raise ResponseShapeError("response has no 'answers' object; inspect the saved raw response")


def _unit(value: Any, what: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ResponseShapeError(f"{what} is not a number: {value!r}") from None
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise ResponseShapeError(f"{what} is outside [0, 1]: {value!r}")
    return number


def _distribution(ans: dict[str, Any], labels: tuple[str, ...]) -> dict[str, float]:
    probs = ans.get("probabilities")
    if not isinstance(probs, dict):
        raise ResponseShapeError(f"answer has no probabilities object (keys: {sorted(ans)})")
    probs = {str(k): v for k, v in probs.items()}
    if set(probs) != set(labels):
        raise ResponseShapeError(f"probabilities cover {sorted(probs)}, expected {sorted(labels)}")
    out = {k: _unit(v, f"probability[{k}]") for k, v in probs.items()}
    if abs(sum(out.values()) - 1.0) > PROBABILITY_TOLERANCE:
        raise ResponseShapeError(f"probabilities sum to {sum(out.values()):.4f}")
    return out


def read_choice(ans: dict[str, Any]) -> tuple[str, float, dict[str, float]]:
    probs = _distribution(ans, VERDICTS)
    choice = ans.get("choice", max(probs, key=probs.get))
    if choice not in VERDICTS:
        raise ResponseShapeError(f"choice {choice!r} is not a verdict")
    confidence = _unit(ans.get("confidence"), "confidence")
    return choice, confidence, probs


def read_score(ans: dict[str, Any]) -> tuple[str, float, dict[str, float]]:
    """Levels are 0=not_met, 1=partial, 2=met; the verdict is the most probable level."""
    raw = _distribution(ans, ("0", "1", "2"))
    order = ("not_met", "partial", "met")
    by_verdict = {order[int(k)]: v for k, v in raw.items()}
    verdict = max(by_verdict, key=by_verdict.get)
    confidence = _unit(ans.get("confidence"), "confidence")
    return verdict, confidence, by_verdict


# TypeSafe documents Noul answers as {"type": "noul", "noul": <yes probability>}.
_NOUL_KEYS = ("noul", "probability", "yes_probability")


def read_noul(ans: dict[str, Any]) -> float:
    """Probability of 'yes'."""
    for key in _NOUL_KEYS:
        if key in ans:
            return _unit(ans[key], f"noul[{key}]")
    raise ResponseShapeError(f"cannot read a yes-probability from Noul answer keys {sorted(ans)}")


def aggregate_requirements(
    requirements: list[dict[str, str]], yes_prob: dict[str, float], cutoff: float = 0.5
) -> tuple[str, float]:
    """All yes -> met; all base yes -> partial; any base no -> not_met.

    Confidence is the weakest requirement decision, max(p, 1 - p). This is not the
    provider's confidence statistic used by variants A and B.
    """
    decided = {req["id"]: yes_prob[req["id"]] >= cutoff for req in requirements}
    if all(decided.values()):
        verdict = "met"
    elif all(decided[req["id"]] for req in requirements if req["role"] == "base"):
        verdict = "partial"
    else:
        verdict = "not_met"
    confidence = min(max(p, 1 - p) for p in (yes_prob[req["id"]] for req in requirements))
    return verdict, confidence


def expected_items(request: dict[str, Any]) -> list[str]:
    """Rubric IDs a request asked about (variant C keys are ``<rubric>__<req>``)."""
    ids: list[str] = []
    for key in request.get("questions", {}):
        rid = key.split("__", 1)[0]
        if rid not in ids:
            ids.append(rid)
    return ids


def item_predictions(
    variant: str,
    response: Any,
    expected: list[str],
    requirements: dict[str, list[dict[str, str]]] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Return (predictions, issues). Every expected item ends up in exactly one of them."""
    preds: dict[str, dict[str, Any]] = {}
    issues: dict[str, str] = {}
    try:
        answers = answers_of(response)
    except ResponseShapeError as exc:
        return {}, {rid: str(exc) for rid in expected}
    if variant == "C" and requirements is None:
        raise ValueError("variant C needs requirements to aggregate")
    reader = read_choice if variant == "A" else read_score
    for rid in expected:
        try:
            if variant in ("A", "B"):
                if rid not in answers:
                    raise ResponseShapeError("item missing from response")
                verdict, confidence, probs = reader(answers[rid])
            else:
                reqs = requirements[rid]
                yes = {}
                for req in reqs:
                    key = f"{rid}__{req['id']}"
                    if key not in answers:
                        raise ResponseShapeError(f"requirement {req['id']} missing from response")
                    yes[req["id"]] = read_noul(answers[key])
                verdict, confidence = aggregate_requirements(reqs, yes)
                probs = yes
        except (ResponseShapeError, KeyError) as exc:
            issues[rid] = str(exc)
            continue
        preds[rid] = {"verdict": verdict, "confidence": confidence, "probabilities": probs}
    extra = {k.split("__", 1)[0] for k in answers} - set(expected)
    for rid in sorted(extra):
        issues.setdefault(f"unexpected:{rid}", "response contains an item that was not asked")
    return preds, issues


def body_sha256(answer: Answer) -> str:
    """Same digest nonsul-review stores as meta.answer_body_sha256."""
    return hashlib.sha256(answer.body.encode("utf-8")).hexdigest()


def infer_sample_set(answer: Answer) -> str:
    """Default grouping when no manifest is given. Never merge sets in a report."""
    if answer.split == "dev":
        return "design"
    if answer.is_alternative:
        return "alternative"
    if answer.error_types:
        return "risk"
    return "normal"
