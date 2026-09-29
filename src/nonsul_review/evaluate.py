"""Transparent, item-level evaluation of independent ratings and model drafts.

All rates have explicit denominators. Abstentions are never recoded as a verdict.
See ``docs/evaluation.md`` for the report schema and formulas.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any

from .io import load_answer, read_json, read_yaml
from .schema import Result, validate_id

VERDICTS = ("not_met", "partial", "met")
_ORDINAL = {value: index for index, value in enumerate(VERDICTS)}
_ERRORS = {"not_met", "partial"}
_REPORT_NAMES = {
    "report.json",
    "evaluation.json",
    "evaluation-report.json",
    "evaluation_report.json",
    "metrics.json",
    "evaluate.json",
    "evaluation.report.json",
}
_SKIP_SUFFIXES = (".raw.json", ".failure.json", ".changes.json", ".final.json")
ItemKey = tuple[str, str]


class EvaluationError(ValueError):
    """An evaluation input is ambiguous, invalid, or unsafe to combine."""


@dataclass(frozen=True)
class _Label:
    verdict: str | None
    unresolved: bool = False


@dataclass
class _Ratings:
    answers: dict[str, str]
    items: dict[ItemKey, _Label]
    alternatives: dict[str, bool]


@dataclass
class _PredictionGroup:
    settings: dict[str, Any]
    answers: dict[str, str]
    items: dict[ItemKey, _Label]
    input_hashes: dict[str, str | None]
    answer_body_hashes: dict[str, str | None]
    group_id: str


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def agreement_metrics(left: Iterable[str], right: Iterable[str]) -> dict[str, Any]:
    """Return exact agreement and linear weighted Cohen kappa for paired labels.

    The ordinal order is not_met < partial < met. A constant, identical pair of
    distributions has undefined kappa (None), including perfect agreement on a
    single category. Inputs must be equally long and already exclude abstentions.
    """
    left_list, right_list = list(left), list(right)
    if len(left_list) != len(right_list):
        raise EvaluationError("Agreement requires equally many paired labels.")
    if any(not isinstance(value, str) or value not in _ORDINAL for value in left_list + right_list):
        raise EvaluationError("Agreement labels must be met, partial, or not_met.")
    n = len(left_list)
    matrix = {a: {b: 0 for b in VERDICTS} for a in VERDICTS}
    for a, b in zip(left_list, right_list):
        matrix[a][b] += 1
    exact = sum(matrix[label][label] for label in VERDICTS)
    left_counts, right_counts = Counter(left_list), Counter(right_list)
    distance_sum = sum(
        matrix[a][b] * abs(_ORDINAL[a] - _ORDINAL[b]) for a in VERDICTS for b in VERDICTS
    )
    expected_distance_numerator = sum(
        left_counts[a] * right_counts[b] * abs(_ORDINAL[a] - _ORDINAL[b])
        for a in VERDICTS
        for b in VERDICTS
    )
    return {
        "compared_items": n,
        "exact_matches": exact,
        "exact_agreement": _ratio(exact, n),
        "linear_weighted_kappa": (
            1 - n * distance_sum / expected_distance_numerator
            if expected_distance_numerator
            else None
        ),
        "observed_weighted_disagreement": _ratio(distance_sum, 2 * n),
        "expected_weighted_disagreement": _ratio(expected_distance_numerator, 2 * n * n),
        "confusion_matrix": matrix,
    }


def _agreement(left: Mapping[ItemKey, _Label], right: Mapping[ItemKey, _Label]) -> dict[str, Any]:
    common = left.keys() & right.keys()
    paired = sorted(
        key
        for key in common
        if not left[key].unresolved
        and not right[key].unresolved
        and left[key].verdict is not None
        and right[key].verdict is not None
    )
    metrics = agreement_metrics(
        [left[key].verdict for key in paired],  # type: ignore[list-item]
        [right[key].verdict for key in paired],  # type: ignore[list-item]
    )
    union = left.keys() | right.keys()
    metrics.update(
        {
            "left_items": len(left),
            "right_items": len(right),
            "union_items": len(union),
            "overlapping_items": len(common),
            "left_only_items": len(left.keys() - right.keys()),
            "right_only_items": len(right.keys() - left.keys()),
            "left_unresolved_items": sum(label.unresolved for label in left.values()),
            "right_unresolved_items": sum(label.unresolved for label in right.values()),
            "left_abstained_items": sum(
                label.verdict is None and not label.unresolved for label in left.values()
            ),
            "right_abstained_items": sum(
                label.verdict is None and not label.unresolved for label in right.values()
            ),
            "overlap_excluded_unresolved_items": sum(
                left[key].unresolved or right[key].unresolved for key in common
            ),
            "overlap_excluded_abstained_items": sum(
                not left[key].unresolved
                and not right[key].unresolved
                and (left[key].verdict is None or right[key].verdict is None)
                for key in common
            ),
            "union_coverage": _ratio(len(paired), len(union)),
        }
    )
    return metrics


def _model_agreement(
    prediction: Mapping[ItemKey, _Label],
    reference: Mapping[ItemKey, _Label],
) -> dict[str, Any]:
    report = _agreement(prediction, reference)
    resolved = {
        key
        for key, label in reference.items()
        if not label.unresolved and label.verdict is not None
    }
    matched = resolved & prediction.keys()
    abstained = sum(prediction[key].verdict is None for key in matched)
    report.update(
        {
            "reference_resolved_items": len(resolved),
            "missing_prediction_items": len(resolved - prediction.keys()),
            "unmatched_prediction_items": len(prediction.keys() - reference.keys()),
            "matched_reference_items": len(matched),
            "prediction_abstentions": abstained,
            "coverage": _ratio(report["compared_items"], len(resolved)),
            "matched_coverage": _ratio(len(matched), len(resolved)),
            "decision_coverage": _ratio(report["compared_items"], len(matched)),
            "end_to_end_exact_agreement": _ratio(report["exact_matches"], len(resolved)),
        }
    )
    return report


def _error_detection(
    prediction: Mapping[ItemKey, _Label],
    gold: Mapping[ItemKey, _Label],
) -> dict[str, Any]:
    resolved = {
        key: label
        for key, label in gold.items()
        if not label.unresolved and label.verdict is not None
    }
    common = resolved.keys() & prediction.keys()
    decided = {key for key in common if prediction[key].verdict is not None}
    abstained = common - decided
    missing = resolved.keys() - prediction.keys()
    tp = fp = fn = tn = 0
    for key in decided:
        predicted_error = prediction[key].verdict in _ERRORS
        actual_error = resolved[key].verdict in _ERRORS
        if predicted_error and actual_error:
            tp += 1
        elif predicted_error:
            fp += 1
        elif actual_error:
            fn += 1
        else:
            tn += 1
    gold_errors = sum(label.verdict in _ERRORS for label in resolved.values())
    return {
        "total_gold_items": len(gold),
        "unresolved_gold_items": sum(label.unresolved for label in gold.values()),
        "unassessable_gold_items": sum(
            label.verdict is None and not label.unresolved for label in gold.values()
        ),
        "resolved_gold_items": len(resolved),
        "gold_error_items": gold_errors,
        "gold_met_items": len(resolved) - gold_errors,
        "prediction_items": len(prediction),
        "matched_resolved_items": len(common),
        "compared_items": len(decided),
        "missing_prediction_items": len(missing),
        "unmatched_prediction_items": len(prediction.keys() - gold.keys()),
        "prediction_abstentions": len(abstained),
        "excluded_unresolved_prediction_items": sum(
            key in prediction and label.unresolved for key, label in gold.items()
        ),
        "excluded_unassessable_prediction_items": sum(
            key in prediction and label.verdict is None and not label.unresolved
            for key, label in gold.items()
        ),
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "true_negatives": tn,
        "precision": _ratio(tp, tp + fp),
        "recall": _ratio(tp, tp + fn),
        "f1": _ratio(2 * tp, 2 * tp + fp + fn),
        "end_to_end_recall": _ratio(tp, gold_errors),
        "missed_errors_due_to_abstention": sum(
            resolved[key].verdict in _ERRORS for key in abstained
        ),
        "missed_errors_due_to_missing_prediction": sum(
            resolved[key].verdict in _ERRORS for key in missing
        ),
        "coverage": _ratio(len(decided), len(resolved)),
        "matched_coverage": _ratio(len(common), len(resolved)),
        "decision_coverage": _ratio(len(decided), len(common)),
    }


def _alternative_false_accusation(
    prediction: Mapping[ItemKey, _Label],
    gold: Mapping[ItemKey, _Label],
    alternatives: Mapping[str, bool],
) -> dict[str, Any]:
    eligible = {
        key
        for key, label in gold.items()
        if alternatives.get(key[0]) is True and not label.unresolved and label.verdict == "met"
    }
    matched = eligible & prediction.keys()
    compared = {key for key in matched if prediction[key].verdict is not None}
    false_accusations = sum(prediction[key].verdict in _ERRORS for key in compared)
    gold_answers = {key[0] for key in gold}
    return {
        "status": "available" if gold_answers & alternatives.keys() else "no_alternative_labels",
        "labelled_gold_answers": len(gold_answers & alternatives.keys()),
        "unlabelled_gold_answers": len(gold_answers - alternatives.keys()),
        "alternative_gold_answers": sum(
            alternatives.get(answer) is True for answer in gold_answers
        ),
        "eligible_gold_met_items": len(eligible),
        "compared_items": len(compared),
        "false_accusations": false_accusations,
        "false_accusation_rate": _ratio(false_accusations, len(compared)),
        "full_set_false_accusation_rate": _ratio(false_accusations, len(eligible)),
        "prediction_abstentions": len(matched - compared),
        "missing_prediction_items": len(eligible - prediction.keys()),
        "coverage": _ratio(len(compared), len(eligible)),
    }


def _read(path: Path, *, json_file: bool = False) -> dict[str, Any]:
    try:
        data = read_json(path) if json_file else read_yaml(path)
    except (OSError, ValueError, TypeError):
        # Do not echo malformed input bodies or a parser's verbatim source snippet.
        raise EvaluationError(f"Cannot safely parse evaluation input: {path}") from None
    if not isinstance(data, dict):
        raise EvaluationError(f"Evaluation input must contain an object: {path}")
    return data


def _nonempty_string(value: Any, label: str, path: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvaluationError(f"{label} must be a nonempty string: {path}")
    try:
        validate_id(value)
    except ValueError:
        raise EvaluationError(f"{label} must be a valid identifier: {path}") from None
    return value


def _register_identity(
    identities: dict[str, str], answer_id: str, problem_id: str, path: Path
) -> None:
    previous = identities.setdefault(answer_id, problem_id)
    if previous != problem_id:
        raise EvaluationError(f"Conflicting problem_id for answer_id {answer_id!r}: {path}")


def _human_record(
    data: Mapping[str, Any], path: Path
) -> tuple[str, str, dict[ItemKey, _Label], bool | None]:
    answer_id = _nonempty_string(data.get("answer_id"), "answer_id", path)
    problem_id = _nonempty_string(data.get("problem_id"), "problem_id", path)
    if not isinstance(data.get("items"), list) or not data["items"]:
        raise EvaluationError(f"Human rating items must be a nonempty list: {path}")
    result: dict[ItemKey, _Label] = {}
    for item in data["items"]:
        if not isinstance(item, dict):
            raise EvaluationError(f"Each human rating item must be an object: {path}")
        rubric_id = _nonempty_string(item.get("rubric_id"), "rubric_id", path)
        unresolved = item.get("unresolved", False)
        if not isinstance(unresolved, bool):
            raise EvaluationError(f"unresolved must be a boolean: {path}")
        if "verdict" not in item and not unresolved:
            raise EvaluationError(f"Each resolved human rating requires verdict: {path}")
        verdict = item.get("verdict")
        if verdict is not None and (not isinstance(verdict, str) or verdict not in VERDICTS):
            raise EvaluationError(f"Human verdict must be met, partial, not_met, or null: {path}")
        key = (answer_id, rubric_id)
        if key in result:
            raise EvaluationError(f"Duplicate rubric_id in human rating: {path}")
        result[key] = _Label(verdict, unresolved)
    alternative = data.get("is_alternative")
    if alternative is not None and not isinstance(alternative, bool):
        raise EvaluationError(f"is_alternative must be a boolean or null: {path}")
    return answer_id, problem_id, result, alternative


def _yaml_files(directory: Path) -> list[Path]:
    return sorted(
        path
        for path in directory.rglob("*")
        if path.is_file() and path.suffix.lower() in {".yaml", ".yml"}
    )


def _load_ratings(
    directory: Path, identities: dict[str, str], *, by_rater: bool
) -> dict[str, _Ratings]:
    ratings: dict[str, _Ratings] = {} if by_rater else {"gold": _Ratings({}, {}, {})}
    for path in _yaml_files(directory):
        relative = path.relative_to(directory)
        if by_rater and len(relative.parts) < 2:
            raise EvaluationError(f"Rater files must be in raters/<rater_id>/<answer>.yaml: {path}")
        rater = relative.parts[0] if by_rater else "gold"
        answer_id, problem_id, items, alternative = _human_record(_read(path), path)
        current = ratings.setdefault(rater, _Ratings({}, {}, {}))
        if answer_id in current.answers:
            raise EvaluationError(f"Duplicate answer_id in {rater!r} ratings: {path}")
        _register_identity(identities, answer_id, problem_id, path)
        current.answers[answer_id] = problem_id
        current.items.update(items)
        if alternative is not None:
            current.alternatives[answer_id] = alternative
    return ratings


def _is_demo(meta: Mapping[str, Any]) -> bool:
    return (
        meta.get("is_demo") is True
        or meta.get("demo") is True
        or str(meta.get("provider", "")).lower() == "demo"
        or str(meta.get("model", "")).lower() == "demo"
        or str(meta.get("model", "")).lower().startswith("demo-")
    )


def _group_settings(mode: str, meta: Mapping[str, Any]) -> dict[str, Any]:
    settings = {
        "mode": mode,
        "provider": meta.get("provider", "unknown"),
        "model": meta["model"],
        "response_models": sorted(set(meta["response_models"]))
        if "response_models" in meta
        else None,
        "prompt_version": meta["prompt_version"],
        "prompt_sha256": meta.get("prompt_sha256"),
        "temperature": float(meta["temperature"]),
        "is_demo": _is_demo(meta),
        "config_sha256": meta.get("config_sha256"),
        "max_tokens": meta.get("max_tokens"),
        "max_retries": meta.get("max_retries"),
        "timeout": float(meta["timeout"])
        if isinstance(meta.get("timeout"), (int, float))
        else meta.get("timeout"),
    }
    # A legacy result may record configuration rather than its digest. Group by
    # that configuration without copying arbitrary configuration (or secrets)
    # into the report. Input hashes deliberately do not define a group.
    if "config" in meta:
        canonical = json.dumps(
            meta["config"],
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        settings["recorded_config_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return settings


def _validate_provenance(meta: Mapping[str, Any], path: Path) -> None:
    for field in ("provider",):
        if field in meta and (not isinstance(meta[field], str) or not meta[field].strip()):
            raise EvaluationError(f"meta.{field} must be a nonempty string: {path}")
    for field in ("is_demo", "demo"):
        if field in meta and not isinstance(meta[field], bool):
            raise EvaluationError(f"meta.{field} must be a boolean: {path}")
    for field in ("config_sha256", "input_sha256", "answer_body_sha256"):
        if field in meta and (
            not isinstance(meta[field], str) or not re.fullmatch(r"[0-9a-f]{64}", meta[field])
        ):
            raise EvaluationError(f"meta.{field} must be a lowercase SHA-256 hex digest: {path}")
    if "response_models" in meta:
        models = meta["response_models"]
        if not isinstance(models, list) or any(
            not isinstance(value, str) or not value.strip() for value in models
        ):
            raise EvaluationError(f"meta.response_models must be a list of model names: {path}")
    if "prompt_sha256" in meta:
        hashes = meta["prompt_sha256"]
        if not isinstance(hashes, dict) or any(
            not isinstance(key, str)
            or not key.strip()
            or not isinstance(value, str)
            or not re.fullmatch(r"[0-9a-f]{64}", value)
            for key, value in hashes.items()
        ):
            raise EvaluationError(
                f"meta.prompt_sha256 must map prompt filenames to SHA-256 digests: {path}"
            )


def _skip_prediction_file(path: Path) -> bool:
    name = path.name.lower()
    return (
        name.endswith(_SKIP_SUFFIXES)
        or name in _REPORT_NAMES
        or name.endswith((".report.json", ".evaluation.json"))
    )


def _load_predictions(
    directory: Path,
    identities: dict[str, str],
    *,
    allow_demo: bool,
) -> tuple[list[_PredictionGroup], dict[str, Any]]:
    groups: dict[str, _PredictionGroup] = {}
    loaded = 0
    skipped = 0
    all_json = sorted(
        path for path in directory.rglob("*") if path.is_file() and path.suffix.lower() == ".json"
    )
    for path in all_json:
        if _skip_prediction_file(path):
            skipped += 1
            continue
        data = _read(path, json_file=True)
        if (
            data.get("report_type") == "nonsul-review-evaluation"
            or data.get("kind") == "batch_manifest"
        ):
            skipped += 1
            continue
        source = data.get("source")
        if (
            data.get("status") == "finalized"
            or "finalized_at" in data
            or isinstance(source, dict)
            and "review_sha256" in source
        ):
            skipped += 1
            continue
        try:
            result = Result.model_validate(data)
        except (TypeError, ValueError):
            raise EvaluationError(f"Invalid common prediction schema: {path}") from None
        data = result.model_dump(mode="json")
        meta = data["meta"]
        _validate_provenance(meta, path)
        if _is_demo(meta) and not allow_demo:
            raise EvaluationError(
                "Demo predictions cannot be used for evaluation without allow_demo=True."
            )
        answer_id, problem_id = data["answer_id"], data["problem_id"]
        _register_identity(identities, answer_id, problem_id, path)
        settings = _group_settings(data["mode"], meta)
        signature = json.dumps(
            settings, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        group = groups.setdefault(
            signature,
            _PredictionGroup(
                settings, {}, {}, {}, {}, hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]
            ),
        )
        if answer_id in group.answers:
            raise EvaluationError(
                f"Duplicate prediction for the same configuration and answer_id: {path}"
            )
        group.answers[answer_id] = problem_id
        group.input_hashes[answer_id] = meta.get("input_sha256")
        group.answer_body_hashes[answer_id] = meta.get("answer_body_sha256")
        for item in data["items"]:
            key = (answer_id, item["rubric_id"])
            if key in group.items:
                raise EvaluationError(f"Duplicate rubric_id in prediction: {path}")
            if item["verdict"] is None and item.get("needs_review") is not True:
                raise EvaluationError(
                    f"A null prediction verdict requires needs_review=true: {path}"
                )
            group.items[key] = _Label(item["verdict"])
        loaded += 1
    return [groups[key] for key in sorted(groups)], {
        "json_files_found": len(all_json),
        "prediction_files_loaded": loaded,
        "non_prediction_files_excluded": skipped,
    }


def _load_metadata(
    directory: Path | None,
    identities: dict[str, str],
) -> tuple[dict[str, set[str]], dict[str, bool], set[str], dict[str, str | None], dict[str, str]]:
    error_types: dict[str, set[str]] = {}
    alternatives: dict[str, bool] = {}
    loaded: set[str] = set()
    splits: dict[str, str | None] = {}
    body_hashes: dict[str, str] = {}
    if directory is None:
        return error_types, alternatives, loaded, splits, body_hashes
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.suffix.lower() != ".md":
            continue
        try:
            answer = load_answer(path)
        except (OSError, ValueError, TypeError):
            raise EvaluationError(f"Invalid answer metadata input: {path}") from None
        if answer.id in loaded:
            raise EvaluationError(f"Duplicate answer_id in answer metadata: {path}")
        _register_identity(identities, answer.id, answer.problem, path)
        loaded.add(answer.id)
        error_types[answer.id] = set(answer.error_types)
        splits[answer.id] = answer.split
        body_hashes[answer.id] = hashlib.sha256(answer.body.encode("utf-8")).hexdigest()
        if "is_alternative" in answer.model_fields_set and answer.is_alternative is not None:
            alternatives[answer.id] = answer.is_alternative
    return error_types, alternatives, loaded, splits, body_hashes


def _subset(items: Mapping[ItemKey, _Label], answer_ids: set[str] | None) -> dict[ItemKey, _Label]:
    return {
        key: label for key, label in items.items() if answer_ids is None or key[0] in answer_ids
    }


def _pairwise(
    raters: Mapping[str, _Ratings], answer_ids: set[str] | None = None
) -> list[dict[str, Any]]:
    return [
        {
            "rater_a": a,
            "rater_b": b,
            **_agreement(
                _subset(raters[a].items, answer_ids), _subset(raters[b].items, answer_ids)
            ),
        }
        for a, b in combinations(sorted(raters), 2)
    ]


def _group_metrics(
    group: _PredictionGroup,
    raters: Mapping[str, _Ratings],
    gold: _Ratings,
    alternatives: Mapping[str, bool],
    answer_ids: set[str] | None = None,
) -> dict[str, Any]:
    prediction = _subset(group.items, answer_ids)
    reference = _subset(gold.items, answer_ids)
    pred_answers = {key[0] for key in prediction}
    gold_answers = {key[0] for key in reference}
    return {
        "prediction_answers": len(pred_answers),
        "gold_answers": len(gold_answers),
        "missing_prediction_answers": len(gold_answers - pred_answers),
        "unmatched_prediction_answers": len(pred_answers - gold_answers),
        "model_rater_agreement": {
            rater: _model_agreement(prediction, _subset(ratings.items, answer_ids))
            for rater, ratings in sorted(raters.items())
        },
        "error_detection": _error_detection(prediction, reference),
        "alternative_false_accusation": _alternative_false_accusation(
            prediction, reference, alternatives
        ),
    }


def _paired_modes(groups: list[_PredictionGroup]) -> list[dict[str, Any]]:
    pairs = []
    for rubric in (group for group in groups if group.settings["mode"] == "rubric"):
        for free in (group for group in groups if group.settings["mode"] == "free"):
            shared = rubric.answers.keys() & free.answers.keys()
            settings_keys = (rubric.settings.keys() | free.settings.keys()) - {
                "mode",
                "prompt_version",
                "prompt_sha256",
            }
            same_settings = all(
                rubric.settings.get(key) == free.settings.get(key) for key in settings_keys
            )
            response_model_verified = (
                isinstance(rubric.settings["response_models"], list)
                and len(rubric.settings["response_models"]) == 1
                and rubric.settings["response_models"] == free.settings["response_models"]
            )
            rubric_common = (rubric.settings["prompt_sha256"] or {}).get("common-v1.txt")
            free_common = (free.settings["prompt_sha256"] or {}).get("common-v1.txt")
            common_prompt_verified = bool(rubric_common) and rubric_common == free_common
            config_hashes_verified = (
                bool(rubric.settings["config_sha256"])
                and rubric.settings["config_sha256"] == free.settings["config_sha256"]
            )
            verified = sum(
                bool(rubric.input_hashes[answer])
                and rubric.input_hashes[answer] == free.input_hashes[answer]
                for answer in shared
            )
            mismatched = sum(
                bool(rubric.input_hashes[answer])
                and bool(free.input_hashes[answer])
                and rubric.input_hashes[answer] != free.input_hashes[answer]
                for answer in shared
            )
            unverified = len(shared) - verified - mismatched
            comparable = (
                same_settings
                and bool(shared)
                and verified == len(shared)
                and response_model_verified
                and common_prompt_verified
                and config_hashes_verified
            )
            pairs.append(
                {
                    "rubric_group_id": rubric.group_id,
                    "free_group_id": free.group_id,
                    "same_recorded_model_and_settings": same_settings,
                    "same_single_response_model_verified": response_model_verified,
                    "same_common_prompt_verified": common_prompt_verified,
                    "same_config_hash_verified": config_hashes_verified,
                    "shared_answers": len(shared),
                    "rubric_only_answers": len(rubric.answers.keys() - free.answers.keys()),
                    "free_only_answers": len(free.answers.keys() - rubric.answers.keys()),
                    "matching_input_hashes": verified,
                    "mismatching_input_hashes": mismatched,
                    "unverified_input_hashes": unverified,
                    "comparable_on_shared_answers": comparable,
                    "fully_paired": comparable and rubric.answers.keys() == free.answers.keys(),
                }
            )
    return pairs


def evaluate_directories(
    pred_dir: Path,
    raters_dir: Path,
    gold_dir: Path,
    answers_dir: Path | None = None,
    *,
    allow_demo: bool = False,
) -> dict[str, Any]:
    """Evaluate original model predictions, independent raters, and resolved gold.

    Only reads files; the caller chooses whether and where to write the report.
    Raw responses, failures, revisions, finalizations, and reports are excluded.
    Missing predictions remain in coverage and end-to-end recall denominators.
    """
    pred_dir, raters_dir, gold_dir = Path(pred_dir), Path(raters_dir), Path(gold_dir)
    answers_dir = Path(answers_dir) if answers_dir is not None else None
    for name, directory in (
        ("pred", pred_dir),
        ("raters", raters_dir),
        ("gold", gold_dir),
        ("answers", answers_dir),
    ):
        if directory is not None and not directory.is_dir():
            raise EvaluationError(f"{name} must be an existing directory: {directory}")
    identities: dict[str, str] = {}
    raters = _load_ratings(raters_dir, identities, by_rater=True)
    gold = _load_ratings(gold_dir, identities, by_rater=False)["gold"]
    groups, file_counts = _load_predictions(pred_dir, identities, allow_demo=allow_demo)
    record_answers = set(identities)
    error_types, metadata_alternatives, metadata_answers, splits, body_hashes = _load_metadata(
        answers_dir, identities
    )
    for group in groups:
        for answer_id, expected_hash in group.answer_body_hashes.items():
            if (
                expected_hash
                and answer_id in body_hashes
                and expected_hash != body_hashes[answer_id]
            ):
                raise EvaluationError(
                    f"Source answer body differs from prediction for answer_id {answer_id!r}."
                )
    split_counts = {
        split: sum(splits.get(answer) == split for answer in record_answers)
        for split in ("dev", "test")
    }
    split_counts["unknown"] = sum(splits.get(answer) is None for answer in record_answers)
    if split_counts["dev"] and split_counts["test"]:
        raise EvaluationError(
            "Evaluation records mix dev and test answers; evaluate each split separately."
        )
    verified_split = next(
        (split for split in ("dev", "test") if split_counts[split] and not split_counts["unknown"]),
        None,
    )
    alternatives = dict(gold.alternatives)
    for answer_id, alternative in metadata_alternatives.items():
        if answer_id in alternatives and alternatives[answer_id] != alternative:
            raise EvaluationError(f"Conflicting is_alternative labels for answer_id {answer_id!r}.")
        alternatives[answer_id] = alternative
    slices: dict[str, set[str]] = {}
    for answer_id, labels in error_types.items():
        if answer_id not in record_answers:
            continue
        for label in labels:
            slices.setdefault(label, set()).add(answer_id)
    report_groups = []
    for group in groups:
        metrics = {
            "group_id": group.group_id,
            "settings": group.settings,
            "answers_with_input_sha256": sum(bool(value) for value in group.input_hashes.values()),
            "answers_without_input_sha256": sum(not value for value in group.input_hashes.values()),
            "source_body_verified_answers": sum(
                bool(value) and answer in body_hashes
                for answer, value in group.answer_body_hashes.items()
            ),
            "source_body_unverifiable_legacy_answers": sum(
                not value for value in group.answer_body_hashes.values()
            ),
            "source_body_unavailable_answers": len(group.answers.keys() - body_hashes.keys()),
            **_group_metrics(group, raters, gold, alternatives),
            "error_type_slices": {
                label: {
                    "metadata_answer_count": len(answer_ids),
                    **_group_metrics(group, raters, gold, alternatives, answer_ids),
                }
                for label, answer_ids in sorted(slices.items())
            },
        }
        report_groups.append(metrics)
    warnings = []
    if not groups:
        warnings.append(
            "No model prediction records were loaded; no model performance estimate is available."
        )
    if not gold.items:
        warnings.append("No gold rating items were loaded; error detection cannot be estimated.")
    if len(raters) < 2:
        warnings.append(
            "Fewer than two independent raters were loaded; inter-rater agreement cannot be estimated."
        )
    if answers_dir is None:
        warnings.append(
            "No answers directory was supplied; answer-metadata error-type slices are unavailable."
        )
    elif record_answers - metadata_answers:
        warnings.append(
            "Some evaluation answers lack source metadata; error-type slices are incomplete."
        )
    if split_counts["unknown"]:
        warnings.append(
            "Some evaluation answers have no verified split metadata; dev/test separation cannot be fully checked."
        )
    if answers_dir is not None and any(
        not value for group in groups for value in group.answer_body_hashes.values()
    ):
        warnings.append(
            "Some legacy predictions lack answer_body_sha256; their match to the supplied answer bodies cannot be verified."
        )
    if any(group.settings["is_demo"] for group in groups):
        warnings.append(
            "This report includes synthetic demo outputs and is not evidence of LLM performance."
        )
    paired_modes = _paired_modes(groups)
    if any(not pair["fully_paired"] for pair in paired_modes):
        warnings.append(
            "Some mode pairs differ in settings, available answers, or verified input hashes; inspect paired_modes before comparison."
        )
    return {
        "report_type": "nonsul-review-evaluation",
        "schema_version": "1.0",
        "ordinal_order": list(VERDICTS),
        "dataset": {
            **file_counts,
            "prediction_groups": len(groups),
            "prediction_answers": len({answer for group in groups for answer in group.answers}),
            "gold_answers": len(gold.answers),
            "gold_items": len(gold.items),
            "rater_count": len(raters),
            "rater_answers": {name: len(rating.answers) for name, rating in sorted(raters.items())},
            "rater_items": {name: len(rating.items) for name, rating in sorted(raters.items())},
            "answer_metadata_loaded": len(metadata_answers),
            "evaluation_answers_missing_metadata": len(record_answers - metadata_answers),
            "metadata_answers_without_evaluation_records": len(metadata_answers - record_answers),
            "answers_with_error_types": sum(
                bool(labels) for answer, labels in error_types.items() if answer in record_answers
            ),
            "answers_with_empty_error_types": sum(
                not labels for answer, labels in error_types.items() if answer in record_answers
            ),
            "answers_with_multiple_error_types": sum(
                len(labels) > 1
                for answer, labels in error_types.items()
                if answer in record_answers
            ),
            "error_type_slice_source": "answer_markdown_front_matter"
            if answers_dir is not None
            else None,
            "error_type_slices_status": (
                "unavailable"
                if answers_dir is None or not metadata_answers
                else "partial"
                if record_answers - metadata_answers
                else "available"
            ),
            "split_counts": split_counts,
            "verified_split": verified_split,
        },
        "inter_rater_agreement": _pairwise(raters),
        "inter_rater_by_error_type": {
            label: {
                "metadata_answer_count": len(answer_ids),
                "pairs": _pairwise(raters, answer_ids),
            }
            for label, answer_ids in sorted(slices.items())
        },
        "groups": report_groups,
        "paired_modes": paired_modes,
        "warnings": warnings,
        "limitations": [
            "Conclusions apply only to this evaluation set; synthetic answers need not represent learner answers.",
            "Agreement and selective precision/recall exclude abstentions and missing predictions; inspect coverage and end_to_end_recall.",
            "Unresolved gold items are excluded even when a provisional verdict is present.",
            "Error-type slices are answer-level, can overlap, and use only Markdown answer metadata.",
            "This descriptive report does not establish statistical significance or causal superiority of either mode.",
        ],
    }
