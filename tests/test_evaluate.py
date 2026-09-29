"""Hand-computed metric and dataset-integrity tests (no external API calls)."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from nonsul_review.evaluate import EvaluationError, agreement_metrics, evaluate_directories


@pytest.fixture
def evaluation_dirs(tmp_path: Path) -> dict[str, Path]:
    result = {name: tmp_path / name for name in ("pred", "raters", "gold", "answers")}
    for path in result.values():
        path.mkdir()
    return result


def _json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _yaml(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, allow_unicode=True), encoding="utf-8")


def _prediction(
    answer: str = "a1",
    labels: dict[str, str | None] | None = None,
    *,
    mode: str = "rubric",
    meta: dict | None = None,
) -> dict:
    return {
        "answer_id": answer,
        "problem_id": "p1",
        "mode": mode,
        "items": [
            {
                "rubric_id": rubric,
                "verdict": verdict,
                "needs_review": verdict is None,
                "evidence": "제시된 조건",
                "reason": "독립적으로 작성한 근거",
                "feedback": "확인할 논증 단계",
            }
            for rubric, verdict in (labels or {"R1": "met"}).items()
        ],
        "overall_feedback": "강사가 검토할 초안입니다.",
        "meta": {
            "provider": "anthropic",
            "model": "fixed-model",
            "prompt_version": f"{mode}-v1",
            "temperature": 0,
            "created_at": "2026-09-29T00:00:00+00:00",
            **(meta or {}),
        },
    }


def _human(
    answer: str = "a1",
    labels: dict[str, str | None] | None = None,
    *,
    unresolved: set[str] | None = None,
    **extra: object,
) -> dict:
    return {
        "answer_id": answer,
        "problem_id": "p1",
        "items": [
            {"rubric_id": rubric, "verdict": verdict, "unresolved": rubric in (unresolved or set())}
            for rubric, verdict in (labels or {"R1": "met"}).items()
        ],
        **extra,
    }


def _answer(
    path: Path,
    answer: str,
    *,
    error_types: list[str] | None = None,
    body: str = "제시된 조건",
    **extra: object,
) -> None:
    metadata = {
        "id": answer,
        "problem": "p1",
        "source": "synthetic",
        "error_types": error_types or [],
        **extra,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n" + yaml.safe_dump(metadata, allow_unicode=True) + "---\n" + body + "\n",
        encoding="utf-8",
    )


def _evaluate(
    dirs: dict[str, Path], *, with_answers: bool = False, allow_demo: bool = False
) -> dict:
    return evaluate_directories(
        dirs["pred"],
        dirs["raters"],
        dirs["gold"],
        dirs["answers"] if with_answers else None,
        allow_demo=allow_demo,
    )


def test_hand_computed_linear_weighted_kappa() -> None:
    report = agreement_metrics(
        ["met", "met", "partial", "partial", "not_met", "not_met"],
        ["met", "partial", "partial", "not_met", "not_met", "met"],
    )
    assert report["exact_agreement"] == 0.5
    assert report["linear_weighted_kappa"] == pytest.approx(0.25)
    assert report["observed_weighted_disagreement"] == pytest.approx(1 / 3)
    assert report["expected_weighted_disagreement"] == pytest.approx(4 / 9)
    assert report["confusion_matrix"]["not_met"]["met"] == 1


@pytest.mark.parametrize(
    ("left", "right", "exact", "kappa"),
    [
        ([], [], None, None),
        (["met", "met"], ["met", "met"], 1.0, None),
        (["met", "met"], ["partial", "partial"], 0.0, 0.0),
        (["not_met", "partial", "met"], ["not_met", "partial", "met"], 1.0, 1.0),
        (["not_met", "met"], ["met", "not_met"], 0.0, -1.0),
    ],
)
def test_empty_constant_perfect_and_disagreeing_kappa(left, right, exact, kappa) -> None:
    report = agreement_metrics(left, right)
    assert report["exact_agreement"] == exact
    assert report["linear_weighted_kappa"] == kappa


@pytest.mark.parametrize(
    ("left", "right"), [(["met"], []), ([None], ["met"]), ([[]], ["met"]), (["bad"], ["met"])]
)
def test_agreement_rejects_invalid_or_unpaired_labels(left, right) -> None:
    with pytest.raises(EvaluationError):
        agreement_metrics(left, right)


def test_abstentions_unresolved_and_missing_predictions_keep_denominators(evaluation_dirs) -> None:
    dirs = evaluation_dirs
    prediction = _prediction(
        labels={
            "R1": "partial",
            "R2": "not_met",
            "R3": "met",
            "R4": "met",
            "R5": None,
            "R7": "not_met",
            "R8": "met",
            "R99": "met",
        }
    )
    gold = _human(
        labels={
            "R1": "not_met",
            "R2": "met",
            "R3": "partial",
            "R4": "met",
            "R5": "partial",
            "R6": "not_met",
            "R7": "met",
            "R8": None,
        },
        unresolved={"R7"},
    )
    _json(dirs["pred"] / "a1.json", prediction)
    _yaml(dirs["gold"] / "a1.yaml", gold)
    _yaml(dirs["raters"] / "alice" / "a1.yaml", gold)
    group = _evaluate(dirs)["groups"][0]
    metrics = group["error_detection"]
    assert metrics["total_gold_items"] == 8
    assert metrics["unresolved_gold_items"] == 1
    assert metrics["unassessable_gold_items"] == 1
    assert metrics["resolved_gold_items"] == 6
    assert metrics["compared_items"] == 4
    assert [
        metrics[key]
        for key in ("true_positives", "false_positives", "false_negatives", "true_negatives")
    ] == [1, 1, 1, 1]
    assert metrics["precision"] == metrics["recall"] == metrics["f1"] == 0.5
    assert metrics["end_to_end_recall"] == 0.25
    assert metrics["coverage"] == pytest.approx(4 / 6)
    assert metrics["prediction_abstentions"] == metrics["missing_prediction_items"] == 1
    assert (
        metrics["missed_errors_due_to_abstention"]
        == metrics["missed_errors_due_to_missing_prediction"]
        == 1
    )
    assert metrics["unmatched_prediction_items"] == 1
    assert metrics["excluded_unresolved_prediction_items"] == 1
    assert metrics["excluded_unassessable_prediction_items"] == 1
    agreement = group["model_rater_agreement"]["alice"]
    assert agreement["exact_agreement"] == 0.25
    assert agreement["end_to_end_exact_agreement"] == pytest.approx(1 / 6)
    assert agreement["coverage"] == pytest.approx(4 / 6)


def test_wholly_missing_answer_is_not_hidden_by_an_inner_join(evaluation_dirs) -> None:
    dirs = evaluation_dirs
    _json(dirs["pred"] / "a1.json", _prediction(labels={"R1": "not_met"}))
    for answer in ("a1", "a2"):
        _yaml(dirs["gold"] / f"{answer}.yaml", _human(answer, {"R1": "partial"}))
    group = _evaluate(dirs)["groups"][0]
    assert group["missing_prediction_answers"] == 1
    assert group["error_detection"]["recall"] == 1
    assert group["error_detection"]["end_to_end_recall"] == 0.5
    assert group["error_detection"]["coverage"] == 0.5


def test_all_abstentions_have_no_selective_estimate_and_zero_end_to_end_recall(
    evaluation_dirs,
) -> None:
    dirs = evaluation_dirs
    _json(dirs["pred"] / "a1.json", _prediction(labels={"R1": None}))
    _yaml(dirs["gold"] / "a1.yaml", _human(labels={"R1": "partial"}))
    report = _evaluate(dirs)["groups"][0]["error_detection"]
    assert report["precision"] is report["recall"] is report["f1"] is None
    assert report["end_to_end_recall"] == 0
    assert report["coverage"] == 0
    assert report["false_negatives"] == 0  # No invented not_met/met verdict.
    assert report["missed_errors_due_to_abstention"] == 1


def test_undefined_error_rates_return_null(evaluation_dirs) -> None:
    dirs = evaluation_dirs
    _json(dirs["pred"] / "a1.json", _prediction())
    _yaml(dirs["gold"] / "a1.yaml", _human())
    report = _evaluate(dirs)["groups"][0]["error_detection"]
    assert report["precision"] is report["recall"] is report["end_to_end_recall"] is None
    assert report["true_negatives"] == 1
    json.dumps(report, allow_nan=False)


def test_pairwise_inter_rater_uses_initial_ratings_and_reports_exclusions(evaluation_dirs) -> None:
    dirs = evaluation_dirs
    _yaml(
        dirs["raters"] / "alice" / "a1.yaml",
        _human(labels={"R1": "met", "R2": "partial", "R3": None}),
    )
    _yaml(
        dirs["raters"] / "bob" / "a1.yaml",
        _human(labels={"R1": "met", "R2": "not_met", "R3": "met", "R4": "met"}),
    )
    _yaml(dirs["raters"] / "carol" / "a1.yaml", _human(labels={"R1": "met"}))
    _yaml(dirs["gold"] / "a1.yaml", _human(labels={"R1": "not_met", "R2": "not_met"}))
    pairs = _evaluate(dirs)["inter_rater_agreement"]
    assert len(pairs) == 3
    pair = next(pair for pair in pairs if pair["rater_a"] == "alice" and pair["rater_b"] == "bob")
    assert pair["compared_items"] == 2
    assert pair["exact_agreement"] == 0.5
    assert pair["right_only_items"] == 1
    assert pair["overlap_excluded_abstained_items"] == 1
    assert pair["union_coverage"] == 0.5


def test_model_and_configuration_groups_cannot_be_pooled(evaluation_dirs) -> None:
    dirs = evaluation_dirs
    configurations = [
        {},
        {"model": "other-model"},
        {"prompt_version": "rubric-v2"},
        {"config_sha256": "a" * 64},
        {"temperature": 0.5},
        {"response_models": ["snapshot-2"]},
        {"prompt_sha256": {"common-v1.txt": "b" * 64}},
    ]
    for index, config in enumerate(configurations):
        _json(dirs["pred"] / f"variant{index}.json", _prediction(meta=config))
    _json(dirs["pred"] / "free.json", _prediction(mode="free"))
    report = _evaluate(dirs)
    assert report["dataset"]["prediction_groups"] == 8
    assert all(group["prediction_answers"] == 1 for group in report["groups"])
    assert len({group["group_id"] for group in report["groups"]}) == 8


def test_per_answer_input_hashes_do_not_split_a_model_group(evaluation_dirs) -> None:
    dirs = evaluation_dirs
    _json(dirs["pred"] / "a1.json", _prediction("a1", meta={"input_sha256": "a" * 64}))
    _json(dirs["pred"] / "a2.json", _prediction("a2", meta={"input_sha256": "b" * 64}))
    report = _evaluate(dirs)
    assert len(report["groups"]) == 1
    assert report["groups"][0]["prediction_answers"] == 2


def test_duplicates_and_numeric_equivalent_temperature_are_rejected(evaluation_dirs) -> None:
    dirs = evaluation_dirs
    _json(dirs["pred"] / "first.json", _prediction(meta={"temperature": 0}))
    _json(dirs["pred"] / "nested" / "second.json", _prediction(meta={"temperature": 0.0}))
    with pytest.raises(EvaluationError, match="Duplicate prediction"):
        _evaluate(dirs)


@pytest.mark.parametrize("kind", ["gold", "rater", "metadata", "human_item", "prediction_item"])
def test_duplicate_ids_are_never_silently_overwritten(evaluation_dirs, kind) -> None:
    dirs = evaluation_dirs
    if kind in {"gold", "rater"}:
        base = dirs["gold"] if kind == "gold" else dirs["raters"] / "alice"
        _yaml(base / "a.yaml", _human())
        _yaml(base / "b.yaml", _human())
    elif kind == "metadata":
        _answer(dirs["answers"] / "a.md", "a1")
        _answer(dirs["answers"] / "b.md", "a1")
    elif kind == "human_item":
        value = _human()
        value["items"].append(copy.deepcopy(value["items"][0]))
        _yaml(dirs["gold"] / "a1.yaml", value)
    else:
        value = _prediction()
        value["items"].append(copy.deepcopy(value["items"][0]))
        _json(dirs["pred"] / "a1.json", value)
    with pytest.raises(EvaluationError):
        _evaluate(dirs, with_answers=True)


def test_duplicate_mapping_keys_are_rejected_without_body_leak(evaluation_dirs) -> None:
    dirs = evaluation_dirs
    secret = "PRIVATE_ANSWER_CANARY"
    (dirs["gold"] / "a.yaml").write_text(f"answer_id: a1\nanswer_id: {secret}\n", encoding="utf-8")
    with pytest.raises(EvaluationError) as error:
        _evaluate(dirs)
    assert secret not in str(error.value)


def test_problem_identity_conflict_is_rejected(evaluation_dirs) -> None:
    dirs = evaluation_dirs
    _json(dirs["pred"] / "a1.json", _prediction())
    gold = _human()
    gold["problem_id"] = "another-problem"
    _yaml(dirs["gold"] / "a1.yaml", gold)
    with pytest.raises(EvaluationError, match="Conflicting problem_id"):
        _evaluate(dirs)


def test_excludes_raw_failure_changes_final_reports_and_renamed_final(evaluation_dirs) -> None:
    dirs = evaluation_dirs
    _json(dirs["pred"] / "a1.json", _prediction())
    for name in (
        "a1.raw.json",
        "a1.failure.json",
        "a1.changes.json",
        "a1.final.json",
        "evaluation.json",
    ):
        (dirs["pred"] / name).write_text("not even valid JSON", encoding="utf-8")
    _json(dirs["pred"] / "renamed_report.json", {"report_type": "nonsul-review-evaluation"})
    _json(
        dirs["pred"] / "batch.rubric.json", {"kind": "batch_manifest", "mode": "rubric", "runs": []}
    )
    _json(dirs["pred"] / "renamed_final.json", {"status": "finalized", **_prediction()})
    report = _evaluate(dirs)
    assert report["dataset"]["prediction_files_loaded"] == 1
    assert report["dataset"]["non_prediction_files_excluded"] == 8


def test_demo_requires_explicit_allowance_and_carries_warning(evaluation_dirs) -> None:
    dirs = evaluation_dirs
    _json(dirs["pred"] / "a1.json", _prediction(meta={"is_demo": True}))
    with pytest.raises(EvaluationError, match="Demo predictions"):
        _evaluate(dirs)
    report = _evaluate(dirs, allow_demo=True)
    assert any("not evidence of LLM performance" in warning for warning in report["warnings"])


def test_error_type_slices_come_only_from_answer_front_matter(evaluation_dirs) -> None:
    dirs = evaluation_dirs
    for answer, gold_verdict in (("a1", "partial"), ("a2", "met")):
        _json(
            dirs["pred"] / f"{answer}.json",
            _prediction(answer, {"R1": "not_met"}, meta={"error_types": ["FAKE"]}),
        )
        _yaml(
            dirs["gold"] / f"{answer}.yaml",
            _human(answer, {"R1": gold_verdict}, error_types=["FAKE"]),
        )
    _answer(dirs["answers"] / "a1.md", "a1", error_types=["E1", "E2"], split="test")
    _answer(dirs["answers"] / "a2.md", "a2", error_types=["E2"], split="test")
    report = _evaluate(dirs, with_answers=True)
    slices = report["groups"][0]["error_type_slices"]
    assert set(slices) == {"E1", "E2"}
    assert slices["E1"]["error_detection"]["precision"] == 1
    assert slices["E2"]["error_detection"]["precision"] == 0.5
    assert report["dataset"]["answers_with_multiple_error_types"] == 1
    assert report["dataset"]["verified_split"] == "test"
    no_sources = _evaluate(dirs)
    assert no_sources["groups"][0]["error_type_slices"] == {}
    assert no_sources["dataset"]["error_type_slice_source"] is None


def test_alternative_false_accusation_respects_abstention_and_correct_gold(evaluation_dirs) -> None:
    dirs = evaluation_dirs
    _json(
        dirs["pred"] / "a1.json",
        _prediction(labels={"R1": "not_met", "R2": "met", "R3": None, "R4": "not_met"}),
    )
    _yaml(
        dirs["gold"] / "a1.yaml",
        _human(
            labels={"R1": "met", "R2": "met", "R3": "met", "R4": "partial", "R5": "met"},
            is_alternative=True,
        ),
    )
    _answer(dirs["answers"] / "a1.md", "a1")  # Absent metadata flag must not override gold true.
    metric = _evaluate(dirs, with_answers=True)["groups"][0]["alternative_false_accusation"]
    assert metric["eligible_gold_met_items"] == 4
    assert metric["compared_items"] == 2
    assert metric["false_accusations"] == 1
    assert metric["false_accusation_rate"] == 0.5
    assert metric["full_set_false_accusation_rate"] == 0.25
    assert metric["coverage"] == 0.5
    assert metric["prediction_abstentions"] == metric["missing_prediction_items"] == 1


def test_alternative_label_conflict_is_rejected(evaluation_dirs) -> None:
    dirs = evaluation_dirs
    _yaml(dirs["gold"] / "a1.yaml", _human(is_alternative=True))
    _answer(dirs["answers"] / "a1.md", "a1", is_alternative=False)
    with pytest.raises(EvaluationError, match="Conflicting is_alternative"):
        _evaluate(dirs, with_answers=True)


def test_unlabelled_answers_do_not_become_alternative_negative_labels(evaluation_dirs) -> None:
    dirs = evaluation_dirs
    _json(dirs["pred"] / "a1.json", _prediction())
    _yaml(dirs["gold"] / "a1.yaml", _human())
    _answer(dirs["answers"] / "a1.md", "a1")
    metric = _evaluate(dirs, with_answers=True)["groups"][0]["alternative_false_accusation"]
    assert metric["status"] == "no_alternative_labels"
    assert metric["labelled_gold_answers"] == 0
    assert metric["false_accusation_rate"] is None


def test_mixed_dev_test_rejected_but_unrelated_metadata_does_not_count(evaluation_dirs) -> None:
    dirs = evaluation_dirs
    _json(dirs["pred"] / "a1.json", _prediction())
    _answer(dirs["answers"] / "a1.md", "a1", split="test")
    _answer(dirs["answers"] / "a2.md", "a2", split="dev", error_types=["UNRELATED"])
    report = _evaluate(dirs, with_answers=True)
    assert report["dataset"]["verified_split"] == "test"
    assert "UNRELATED" not in report["groups"][0]["error_type_slices"]
    _yaml(dirs["gold"] / "a2.yaml", _human("a2"))
    with pytest.raises(EvaluationError, match="mix dev and test"):
        _evaluate(dirs, with_answers=True)


def test_source_answer_body_hash_is_checked_before_metadata_join(evaluation_dirs) -> None:
    dirs = evaluation_dirs
    body = "제시된 조건"
    _json(
        dirs["pred"] / "a1.json",
        _prediction(meta={"answer_body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest()}),
    )
    _answer(dirs["answers"] / "a1.md", "a1", body=body)
    assert _evaluate(dirs, with_answers=True)["groups"][0]["source_body_verified_answers"] == 1
    _answer(dirs["answers"] / "a1.md", "a1", body="수정된 답안 본문")
    with pytest.raises(EvaluationError, match="Source answer body differs"):
        _evaluate(dirs, with_answers=True)


def test_paired_modes_verify_real_model_common_prompt_config_and_input(evaluation_dirs) -> None:
    dirs = evaluation_dirs
    shared = {
        "response_models": ["snapshot-1"],
        "config_sha256": "a" * 64,
        "input_sha256": "b" * 64,
    }
    for mode in ("rubric", "free"):
        _json(
            dirs["pred"] / f"{mode}.json",
            _prediction(
                mode=mode,
                meta={
                    **shared,
                    "prompt_sha256": {"common-v1.txt": "c" * 64, f"{mode}-v1.txt": "d" * 64},
                },
            ),
        )
    pair = _evaluate(dirs)["paired_modes"][0]
    assert pair["fully_paired"] is True
    assert pair["same_single_response_model_verified"] is True
    altered = _prediction(
        mode="free",
        meta={
            **shared,
            "response_models": ["snapshot-2"],
            "input_sha256": "e" * 64,
            "prompt_sha256": {"common-v1.txt": "f" * 64},
        },
    )
    _json(dirs["pred"] / "free.json", altered)
    pair = _evaluate(dirs)["paired_modes"][0]
    assert pair["fully_paired"] is False
    assert pair["same_single_response_model_verified"] is False
    assert pair["same_common_prompt_verified"] is False
    assert pair["mismatching_input_hashes"] == 1


def test_legacy_modes_are_not_claimed_verified_comparable(evaluation_dirs) -> None:
    dirs = evaluation_dirs
    for mode in ("rubric", "free"):
        _json(dirs["pred"] / f"{mode}.json", _prediction(mode=mode))
    pair = _evaluate(dirs)["paired_modes"][0]
    assert pair["same_recorded_model_and_settings"] is True
    assert pair["comparable_on_shared_answers"] is False
    assert pair["unverified_input_hashes"] == 1


def test_empty_datasets_warn_instead_of_fabricating_performance(evaluation_dirs) -> None:
    report = _evaluate(evaluation_dirs)
    assert report["groups"] == []
    assert report["inter_rater_agreement"] == []
    assert any("No model prediction" in warning for warning in report["warnings"])
    assert any("No gold rating" in warning for warning in report["warnings"])


def test_bad_null_prediction_and_misplaced_rater_file_rejected(evaluation_dirs) -> None:
    dirs = evaluation_dirs
    prediction = _prediction(labels={"R1": None})
    prediction["items"][0]["needs_review"] = False
    _json(dirs["pred"] / "a1.json", prediction)
    with pytest.raises(EvaluationError, match="prediction schema"):
        _evaluate(dirs)
    (dirs["pred"] / "a1.json").unlink()
    _yaml(dirs["raters"] / "a1.yaml", _human())
    with pytest.raises(EvaluationError, match="raters/<rater_id>"):
        _evaluate(dirs)
