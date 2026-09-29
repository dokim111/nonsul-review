"""Independent acceptance checks derived from the user's file contract.

These tests deliberately use no API credentials and make no live model calls.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from nonsul_review.io import (
    InputError,
    load_answer,
    load_problem,
    parse_yaml,
    read_json,
    write_json,
)
from nonsul_review.schema import (
    Answer,
    JudgementItem,
    Problem,
    Result,
    validate_result_context,
)


def _problem() -> Problem:
    return Problem.model_validate(
        {
            "id": "spec-001",
            "title": "Boundary condition",
            "prompt": "f'(x)=2x and f(1)=3. Find f(x) and justify your answer.",
            "model_answer": "f(x)=x^2+C; f(1)=3 gives C=2, hence f(x)=x^2+2.",
            "rubric": [
                {
                    "id": "R1",
                    "step": "Find an antiderivative",
                    "criteria": "Integrate 2x, with or without a definite integral.",
                    "partial": "Correct power but wrong coefficient.",
                },
                {
                    "id": "R2",
                    "step": "Apply the boundary condition",
                    "criteria": "Use f(1)=3 correctly.",
                    "partial": "Set up the correct relation but compute it incorrectly.",
                },
            ],
            "alternatives": "f(x)-f(1)=integral from 1 to x of 2t dt=x^2-1.",
        }
    )


def _answer() -> Answer:
    return Answer.model_validate(
        {
            "id": "answer-id-PRIVATE_SENTINEL",
            "problem": "spec-001",
            "source": "learner",
            "error_types": ["E_PRIVATE_SENTINEL"],
            "is_alternative": True,
            "split": "test",
            "body": "f(x)=x^2+C. Since f(1)=3, C=2. Thus f(x)=x^2+2.",
        }
    )


def _item(rubric_id: str, **overrides: object) -> dict[str, object]:
    return {
        "rubric_id": rubric_id,
        "verdict": "met",
        "evidence": "f(x)=x^2+C.",
        "reason": "The integration is correct.",
        "feedback": "Keep the boundary substitution explicit.",
        **overrides,
    }


def _result(items: list[dict[str, object]] | None = None) -> Result:
    return Result.model_validate(
        {
            "answer_id": _answer().id,
            "problem_id": _problem().id,
            "mode": "rubric",
            "items": items if items is not None else [_item("R1"), _item("R2")],
            "overall_feedback": "The conclusion follows from integration and the boundary value.",
            "meta": {
                "model": "unit-test-model",
                "prompt_version": "rubric-v1",
                "temperature": 0,
                "created_at": "2026-09-29T08:00:00+09:00",
            },
        }
    )


@pytest.mark.parametrize(
    "document",
    [
        "id: first\nid: second\n",
        "rubric:\n  - id: R1\n    criteria: first\n    criteria: second\n",
        "true: first\n1: second\n",
    ],
)
def test_yaml_duplicate_keys_never_silently_change_inputs(document: str) -> None:
    with pytest.raises(InputError, match="duplicate"):
        parse_yaml(document)


@pytest.mark.parametrize(
    "document",
    [
        "first: &shared [a, b]\nsecond: *shared\n",
        "recursive: &recursive [*recursive]\n",
        "x: !!python/object/apply:builtins.eval ['1+1']\n",
    ],
)
def test_yaml_aliases_and_object_construction_are_rejected(document: str) -> None:
    with pytest.raises(InputError):
        parse_yaml(document)


@pytest.mark.parametrize(
    "identifier",
    ["../outside", "nested/file", "nested\\file", "/absolute", "..", "CON", "NUL.txt"],
)
def test_answer_ids_cannot_escape_output_or_use_reserved_names(identifier: str) -> None:
    data = _answer().model_dump()
    data["id"] = identifier
    with pytest.raises(ValidationError):
        Answer.model_validate(data)


def test_front_matter_is_data_separate_from_bom_crlf_answer_body(tmp_path: Path) -> None:
    path = tmp_path / "a.md"
    path.write_bytes(
        (
            "\ufeff---\r\n"
            "id: answer-id-PRIVATE_SENTINEL\r\n"
            "problem: spec-001\r\n"
            "source: learner\r\n"
            "error_types: [E_PRIVATE_SENTINEL]\r\n"
            "is_alternative: true\r\n"
            "split: test\r\n"
            "---\r\n"
            "The actual mathematical answer.\r\n"
        ).encode("utf-8")
    )
    answer = load_answer(path)
    assert answer.body == "The actual mathematical answer."
    assert answer.id == "answer-id-PRIVATE_SENTINEL"
    assert answer.error_types == ["E_PRIVATE_SENTINEL"]
    assert answer.is_alternative is True
    assert answer.split == "test"


def test_front_matter_cannot_override_the_actual_answer(tmp_path: Path) -> None:
    path = tmp_path / "a.md"
    path.write_text(
        "---\nid: a01\nproblem: spec-001\nsource: synthetic\nbody: fabricated\n"
        "---\nThe real answer.\n",
        encoding="utf-8",
    )
    with pytest.raises(InputError, match="body must be after"):
        load_answer(path)


def test_validation_error_does_not_echo_private_invalid_value(tmp_path: Path) -> None:
    path = tmp_path / "problem.yaml"
    data = _problem().model_dump()
    data["rubric"][0]["points"] = "PRIVATE_ANSWER_OR_SECRET_TOKEN"
    # JSON is also valid YAML; this avoids a separate serialization dependency here.
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(InputError) as error:
        load_problem(path)
    assert "PRIVATE_ANSWER_OR_SECRET_TOKEN" not in str(error.value)
    assert "points" in str(error.value)


@pytest.mark.parametrize("document", ['{"x": 1, "x": 2}', '{"x": NaN}', '{"x": Infinity}'])
def test_noncanonical_json_is_not_silently_accepted(tmp_path: Path, document: str) -> None:
    path = tmp_path / "result.json"
    path.write_text(document, encoding="utf-8")
    with pytest.raises(InputError):
        read_json(path)


def test_result_requires_exact_complete_rubric_membership() -> None:
    for items in ([_item("R1")], [_item("R1"), _item("R99")]):
        with pytest.raises(ValueError, match="exactly"):
            validate_result_context(_result(items), _problem(), _answer())
    with pytest.raises(ValidationError, match="unique"):
        _result([_item("R1"), _item("R1")])


def test_result_cannot_quote_unwritten_answer_text() -> None:
    result = _result([_item("R1", evidence="The student did not write this."), _item("R2")])
    with pytest.raises(ValueError, match="verbatim"):
        validate_result_context(result, _problem(), _answer())


def test_uncertainty_is_not_coerced_to_a_mathematical_error() -> None:
    data = _item("R1", verdict=None, evidence="", needs_review=True, reason="Insufficient context.")
    undecidable = JudgementItem.model_validate(data)
    assert undecidable.verdict is None
    assert undecidable.needs_review is True
    data["needs_review"] = False
    with pytest.raises(ValidationError, match="undecidable"):
        JudgementItem.model_validate(data)


def test_nonoverwrite_output_preserves_the_original_complete_artifact(tmp_path: Path) -> None:
    path = tmp_path / "result.json"
    write_json(path, {"generation": 1})
    with pytest.raises(FileExistsError):
        write_json(path, {"generation": 2})
    assert read_json(path) == {"generation": 1}
    assert sorted(child.name for child in tmp_path.iterdir()) == ["result.json"]


def test_output_refuses_a_dangling_symlink(tmp_path: Path) -> None:
    target = tmp_path / "outside.json"
    path = tmp_path / "result.json"
    try:
        path.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable in this environment")
    with pytest.raises(InputError, match="symbolic"):
        write_json(path, {"generation": 1})
    assert not target.exists()


def test_linear_weighted_kappa_matches_independent_fraction_calculation() -> None:
    from nonsul_review.evaluate import agreement_metrics

    report = agreement_metrics(
        ["met", "met", "partial", "partial", "not_met", "not_met"],
        ["met", "partial", "partial", "not_met", "not_met", "met"],
    )
    assert report["exact_agreement"] == pytest.approx(1 / 2)
    assert report["observed_weighted_disagreement"] == pytest.approx(1 / 3)
    assert report["expected_weighted_disagreement"] == pytest.approx(4 / 9)
    assert report["linear_weighted_kappa"] == pytest.approx(1 / 4)
    assert agreement_metrics(["met", "met"], ["met", "met"])["linear_weighted_kappa"] is None
    assert agreement_metrics([], [])["exact_agreement"] is None


def _evaluation_dirs(tmp_path: Path) -> tuple[Path, Path, Path]:
    pred, raters, gold = (tmp_path / name for name in ("pred", "raters", "gold"))
    pred.mkdir()
    (raters / "rater-A").mkdir(parents=True)
    gold.mkdir()
    return pred, raters, gold


def _human_labels(path: Path, labels: list[dict[str, object]], **extra: object) -> None:
    path.write_text(
        json.dumps(
            {
                "answer_id": _answer().id,
                "problem_id": _problem().id,
                "items": labels,
                **extra,
            }
        ),
        encoding="utf-8",
    )


def test_error_detection_keeps_uncertainty_and_unresolved_out_of_error_counts(
    tmp_path: Path,
) -> None:
    from nonsul_review.evaluate import evaluate_directories

    pred, raters, gold = _evaluation_dirs(tmp_path)
    gold_labels = [
        {"rubric_id": "R1", "verdict": "not_met"},
        {"rubric_id": "R2", "verdict": "partial"},
        {"rubric_id": "R3", "verdict": "met"},
        {"rubric_id": "R4", "verdict": "met"},
        {"rubric_id": "R5", "verdict": "not_met"},
        {"rubric_id": "R6", "verdict": "partial", "unresolved": True},
        {"rubric_id": "R7", "verdict": "not_met"},
        {"rubric_id": "R8", "verdict": "met"},
    ]
    _human_labels(gold / "a.yaml", gold_labels, is_alternative=True)
    _human_labels(
        raters / "rater-A" / "a.yaml",
        [{"rubric_id": row["rubric_id"], "verdict": row["verdict"]} for row in gold_labels],
    )
    prediction = _result(
        [
            _item("R1", verdict="partial"),
            _item("R2", verdict="met"),
            _item("R3", verdict="not_met"),
            _item("R4", verdict="met"),
            _item("R5", verdict=None, needs_review=True, reason="Cannot establish this step."),
            _item("R6", verdict="not_met"),
        ]
    )
    write_json(pred / "a.rubric.json", prediction.model_dump(mode="json"))
    report = evaluate_directories(pred, raters, gold)
    metrics = report["groups"][0]["error_detection"]
    assert metrics["total_gold_items"] == 8
    assert metrics["unresolved_gold_items"] == 1
    assert metrics["resolved_gold_items"] == 7
    assert metrics["prediction_abstentions"] == 1
    assert metrics["missing_prediction_items"] == 2
    assert metrics["true_positives"] == metrics["false_positives"] == 1
    assert metrics["false_negatives"] == metrics["true_negatives"] == 1
    assert metrics["precision"] == metrics["recall"] == pytest.approx(0.5)
    assert metrics["end_to_end_recall"] == pytest.approx(0.25)
    assert metrics["coverage"] == pytest.approx(4 / 7)
    assert metrics["missed_errors_due_to_abstention"] == 1
    assert metrics["missed_errors_due_to_missing_prediction"] == 1
    alternative = report["groups"][0]["alternative_false_accusation"]
    assert alternative["eligible_gold_met_items"] == 3
    assert alternative["false_accusations"] == 1
    assert alternative["false_accusation_rate"] == pytest.approx(1 / 2)
    assert alternative["coverage"] == pytest.approx(2 / 3)
    # A consensus-unresolved label still exists in the independent first rating.
    agreement = report["groups"][0]["model_rater_agreement"]["rater-A"]
    assert agreement["compared_items"] == 5
    assert agreement["exact_agreement"] == pytest.approx(1 / 5)


def test_evaluation_never_silently_combines_different_model_settings(tmp_path: Path) -> None:
    from nonsul_review.evaluate import evaluate_directories

    pred, raters, gold = _evaluation_dirs(tmp_path)
    labels = [{"rubric_id": "R1", "verdict": "met"}, {"rubric_id": "R2", "verdict": "met"}]
    _human_labels(gold / "a.yaml", labels)
    _human_labels(raters / "rater-A" / "a.yaml", labels)
    baseline = _result().model_dump(mode="json")
    baseline["meta"]["input_sha256"] = "a" * 64
    write_json(pred / "a.model-one.json", baseline)
    different = json.loads(json.dumps(baseline))
    different["mode"] = "free"
    different["meta"]["prompt_version"] = "free-v1"
    different["meta"]["model"] = "other-model"
    write_json(pred / "a.model-two.json", different)
    report = evaluate_directories(pred, raters, gold)
    assert len(report["groups"]) == 2
    assert report["paired_modes"][0]["same_recorded_model_and_settings"] is False
    assert report["paired_modes"][0]["comparable_on_shared_answers"] is False


def test_evaluation_rejects_demo_predictions_without_explicit_opt_in(tmp_path: Path) -> None:
    from nonsul_review.evaluate import EvaluationError, evaluate_directories

    pred, raters, gold = _evaluation_dirs(tmp_path)
    data = _result().model_dump(mode="json")
    data["meta"].update(provider="demo", is_demo=True)
    write_json(pred / "a.json", data)
    with pytest.raises(EvaluationError, match="Demo"):
        evaluate_directories(pred, raters, gold)
    report = evaluate_directories(pred, raters, gold, allow_demo=True)
    assert any("not evidence of LLM performance" in message for message in report["warnings"])


class _ScriptedClient:
    """A finite response script; cannot make an HTTP request."""

    def __init__(self, replies: list[dict[str, object] | str | Exception]):
        self.replies = list(replies)
        self.requests: list[dict[str, object]] = []
        self.messages = self

    def create(self, **request: object) -> dict[str, object]:
        self.requests.append(request)
        if not self.replies:
            raise AssertionError("The pipeline exceeded the scripted request count")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return {
            "id": "local-scripted-response",
            "type": "message",
            "role": "assistant",
            "model": "unit-test-model",
            "stop_reason": "end_turn",
            "content": [
                {"type": "text", "text": reply if isinstance(reply, str) else json.dumps(reply)}
            ],
        }


def _request_envelope(request: dict[str, object]) -> dict[str, object]:
    return json.loads(request["messages"][0]["content"])


def test_modes_share_all_actual_source_information_but_execute_in_opposite_order() -> None:
    from nonsul_review.llm import RunConfig, run_review

    rubric = _ScriptedClient([_item("R1"), _item("R2"), {"overall_feedback": "Rubric synthesis."}])
    free = _ScriptedClient(
        [{"overall_feedback": "Holistic feedback first."}, {"items": [_item("R1"), _item("R2")]}]
    )
    config = RunConfig(model="unit-test-model", temperature=0.4, max_tokens=2048)
    rubric_result, rubric_trace = run_review(_problem(), _answer(), "rubric", config, client=rubric)
    free_result, free_trace = run_review(_problem(), _answer(), "free", config, client=free)
    expected_source = {"problem": _problem().model_dump(mode="json"), "answer_body": _answer().body}
    for request in rubric.requests + free.requests:
        envelope = _request_envelope(request)
        assert envelope["source"] == expected_source
        actual_input = json.dumps(request)
        assert "answer-id-PRIVATE_SENTINEL" not in actual_input
        assert "E_PRIVATE_SENTINEL" not in actual_input
        assert '"is_alternative"' not in actual_input
        assert '"split"' not in actual_input
        assert request["model"] == "unit-test-model"
        assert request["extra_body"] == {"temperature": 0.4}
        assert request["max_tokens"] == 2048
    assert [_request_envelope(row)["workflow"]["stage"] for row in rubric.requests] == [
        "rubric:R1",
        "rubric:R2",
        "rubric:feedback",
    ]
    assert [_request_envelope(row)["workflow"]["stage"] for row in free.requests] == [
        "free:feedback",
        "free:table",
    ]
    assert free_result.overall_feedback == "Holistic feedback first."
    assert rubric_result.meta["input_sha256"] == free_result.meta["input_sha256"]
    assert rubric_result.meta["config_sha256"] == free_result.meta["config_sha256"]
    assert rubric_trace["status"] == free_trace["status"] == "succeeded"


@pytest.mark.parametrize(
    "bad_items",
    [
        [_item("R1")],
        [_item("R1"), _item("R99")],
        [_item("R1"), _item("R1")],
    ],
    ids=["missing", "unknown", "duplicate"],
)
def test_pipeline_retries_invalid_rubric_membership_twice_and_preserves_every_response(
    bad_items: list[dict[str, object]],
) -> None:
    from nonsul_review.llm import PipelineError, RunConfig, run_review

    bad_response = {"items": bad_items}
    client = _ScriptedClient(
        [{"overall_feedback": "Feedback completed before table."}] + [bad_response] * 3
    )
    with pytest.raises(PipelineError) as error:
        run_review(_problem(), _answer(), "free", RunConfig(model="unit-test-model"), client=client)
    assert len(client.requests) == 4
    trace = error.value.raw
    assert trace["status"] == "failed"
    assert len(trace["stages"]) == 2
    first, failed = trace["stages"]
    assert len(first["attempts"]) == 1
    assert first["attempts"][0]["valid"] is True
    assert len(failed["attempts"]) == 3
    assert all(attempt["valid"] is False for attempt in failed["attempts"])
    assert all(attempt["response"]["content"] for attempt in failed["attempts"])
    assert [attempt["attempt"] for attempt in failed["attempts"]] == [1, 2, 3]


@pytest.mark.parametrize(
    "bad_reply",
    [
        '{"overall_feedback":' + "[" * 1100 + "0" + "]" * 1100 + "}",
        {"overall_feedback": "x" * 200_001},
    ],
    ids=["excessive-json-nesting", "oversized-feedback"],
)
def test_pathological_model_output_keeps_the_bounded_failure_trace(
    bad_reply: dict[str, object] | str,
) -> None:
    from nonsul_review.llm import PipelineError, RunConfig, run_review

    client = _ScriptedClient([bad_reply] * 3)
    with pytest.raises(PipelineError) as error:
        run_review(_problem(), _answer(), "free", RunConfig(model="unit-test-model"), client=client)
    assert len(client.requests) == 3
    assert error.value.raw["status"] == "failed"
    assert len(error.value.raw["stages"][0]["attempts"]) == 3


def test_provider_echo_and_exception_do_not_persist_the_environment_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nonsul_review.llm import PipelineError, RunConfig, run_review

    secret = "UNIT_TEST_ENV_SECRET_9876543210"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    echoed = _ScriptedClient(
        [
            {"overall_feedback": f"Provider echoed {secret}."},
            {"items": [_item("R1", reason=f"Echoed {secret}."), _item("R2")]},
        ]
    )
    result, trace = run_review(
        _problem(), _answer(), "free", RunConfig(model="unit-test-model"), client=echoed
    )
    assert secret not in json.dumps(result.model_dump(mode="json"))
    assert secret not in json.dumps(trace)
    failed = _ScriptedClient([RuntimeError(f"HTTP failure containing {secret}")])
    with pytest.raises(PipelineError) as error:
        run_review(_problem(), _answer(), "free", RunConfig(model="unit-test-model"), client=failed)
    assert secret not in str(error.value)
    assert secret not in json.dumps(error.value.raw)


@pytest.mark.parametrize("difference", ["response_model", "common_prompt"])
def test_matching_aliases_do_not_hide_actual_model_or_common_prompt_changes(
    tmp_path: Path,
    difference: str,
) -> None:
    from nonsul_review.evaluate import evaluate_directories

    pred, raters, gold = _evaluation_dirs(tmp_path)
    first = _result().model_dump(mode="json")
    first["meta"].update(
        input_sha256="a" * 64,
        response_models=["actual-model-20260101"],
        prompt_sha256={"common-v1.txt": "c" * 64, "rubric-item-v1.txt": "d" * 64},
    )
    second = json.loads(json.dumps(first))
    second["mode"] = "free"
    second["meta"]["prompt_version"] = "free-v1"
    second["meta"]["prompt_sha256"] = {"common-v1.txt": "c" * 64, "free-table-v1.txt": "e" * 64}
    if difference == "response_model":
        second["meta"]["response_models"] = ["actual-model-20260929"]
    else:
        second["meta"]["prompt_sha256"]["common-v1.txt"] = "f" * 64
    write_json(pred / "a.rubric.json", first)
    write_json(pred / "a.free.json", second)
    report = evaluate_directories(pred, raters, gold)
    assert report["paired_modes"][0]["comparable_on_shared_answers"] is False


def test_evaluation_rejects_new_answer_body_joined_to_old_prediction(tmp_path: Path) -> None:
    import hashlib

    from nonsul_review.evaluate import EvaluationError, evaluate_directories

    pred, raters, gold = _evaluation_dirs(tmp_path)
    answers = tmp_path / "answers"
    answers.mkdir()
    original = _answer()
    data = _result().model_dump(mode="json")
    data["meta"]["answer_body_sha256"] = hashlib.sha256(original.body.encode("utf-8")).hexdigest()
    write_json(pred / "a.rubric.json", data)
    (answers / "a.md").write_text(
        "---\n"
        f"id: {original.id}\n"
        f"problem: {original.problem}\n"
        "source: learner\nsplit: test\n"
        "---\n"
        "A different answer replaced the original, but reused its identifier.\n",
        encoding="utf-8",
    )
    with pytest.raises(EvaluationError):
        evaluate_directories(pred, raters, gold, answers_dir=answers)


def test_evaluation_refuses_mixed_dev_and_test_metadata(tmp_path: Path) -> None:
    from nonsul_review.evaluate import EvaluationError, evaluate_directories

    pred, raters, gold = _evaluation_dirs(tmp_path)
    answers = tmp_path / "answers"
    answers.mkdir()
    for split in ("dev", "test"):
        data = _result().model_dump(mode="json")
        data["answer_id"] = f"answer-{split}"
        write_json(pred / f"{split}.json", data)
        (answers / f"{split}.md").write_text(
            "---\n"
            f"id: answer-{split}\n"
            "problem: spec-001\nsource: learner\n"
            f"split: {split}\n"
            "---\nA mathematical answer.\n",
            encoding="utf-8",
        )
    with pytest.raises(EvaluationError, match="[Dd]ev.*test|test.*dev"):
        evaluate_directories(pred, raters, gold, answers_dir=answers)


def test_changing_only_evaluation_labels_never_changes_model_input_or_fingerprints() -> None:
    from nonsul_review.llm import RunConfig, run_review

    baseline = _answer()
    changed_data = baseline.model_dump()
    changed_data.update(
        id="different-answer-label",
        source="synthetic",
        error_types=["E_ANOTHER_LABEL"],
        is_alternative=False,
        split="dev",
    )
    changed = Answer.model_validate(changed_data)
    clients = [
        _ScriptedClient(
            [
                {"overall_feedback": "Identical scripted response."},
                {"items": [_item("R1"), _item("R2")]},
            ]
        )
        for _ in range(2)
    ]
    results = [
        run_review(_problem(), answer, "free", RunConfig(model="unit-test-model"), client=client)[0]
        for answer, client in zip((baseline, changed), clients)
    ]
    assert clients[0].requests == clients[1].requests
    assert results[0].items == results[1].items
    assert results[0].overall_feedback == results[1].overall_feedback
    assert results[0].meta["input_sha256"] == results[1].meta["input_sha256"]
    assert results[0].meta["config_sha256"] == results[1].meta["config_sha256"]


def test_multiple_actual_models_across_stages_cannot_be_declared_comparable(tmp_path: Path) -> None:
    from nonsul_review.evaluate import evaluate_directories

    pred, raters, gold = _evaluation_dirs(tmp_path)
    data = _result().model_dump(mode="json")
    data["meta"].update(
        input_sha256="a" * 64,
        response_models=["actual-model-one", "actual-model-two"],
        prompt_sha256={"common-v1.txt": "c" * 64},
    )
    write_json(pred / "a.rubric.json", data)
    data["mode"] = "free"
    data["meta"]["prompt_version"] = "free-v1"
    write_json(pred / "a.free.json", data)
    report = evaluate_directories(pred, raters, gold)
    assert report["paired_modes"][0]["comparable_on_shared_answers"] is False


def _release_checker():
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "scripts" / "release_check.py"
    specification = importlib.util.spec_from_file_location("independent_release_check", path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _deliberately_invalid_live_manifest(tmp_path: Path):
    """Local rejection fixture: its empty raw stages cannot prove an API run."""
    import hashlib

    from nonsul_review import __version__
    from nonsul_review.llm import RunConfig, config_fingerprint, input_fingerprint

    checker = _release_checker()
    root = tmp_path / "release-fixture"
    root.mkdir()
    (root / "pyproject.toml").write_text("# Invalid local acceptance fixture.\n", encoding="utf-8")
    problem_data = _problem().model_dump(mode="json")
    problem_data["id"] = "ex-001"
    problem = Problem.model_validate(problem_data)
    answer_data = _answer().model_dump(mode="json")
    answer_data.update(id="ex-001-a01", problem="ex-001", source="synthetic")
    answer = Answer.model_validate(answer_data)
    problem_path, answer_path = root / "problem.yaml", root / "answer.md"
    problem_path.write_text(json.dumps(problem_data), encoding="utf-8")
    metadata = {key: value for key, value in answer_data.items() if key != "body"}
    answer_path.write_text(
        "---\n" + json.dumps(metadata) + "\n---\n" + answer.body + "\n", encoding="utf-8"
    )
    result = _result().model_dump(mode="json")
    result.update(answer_id=answer.id, problem_id=problem.id, mode="free")
    config = RunConfig(model="unit-test-model", temperature=0)
    result["meta"].update(
        provider="anthropic",
        is_demo=False,
        prompt_version="free-v2",
        input_sha256=input_fingerprint(problem, answer.body),
        answer_body_sha256=hashlib.sha256(answer.body.encode("utf-8")).hexdigest(),
        config_sha256=config_fingerprint(config, "anthropic"),
        max_tokens=config.max_tokens,
        timeout=config.timeout,
        max_retries=config.max_retries,
    )
    result_path, raw_path = root / "result.json", root / "result.raw.json"
    write_json(result_path, result)
    write_json(
        raw_path,
        {
            "schema_version": "1.0",
            "provider": "anthropic",
            "is_demo": False,
            "status": "succeeded",
            "mode": "free",
            "prompt_version": "free-v2",
            "created_at": result["meta"]["created_at"],
            "model": result["meta"]["model"],
            "input_sha256": result["meta"]["input_sha256"],
            "config_sha256": result["meta"]["config_sha256"],
            "stages": [],  # Must be rejected: no actual or simulated API message is asserted.
        },
    )
    descriptors = {
        kind: {"path": path.name, "sha256": checker.sha256_file(path)}
        for kind, path in (
            ("problem", problem_path),
            ("answer", answer_path),
            ("result", result_path),
            ("raw", raw_path),
        )
    }
    manifest = {
        "schema_version": "1.0",
        "tool_version": __version__,
        "is_demo": False,
        "provider": "anthropic",
        "created_at": "2026-09-29T08:00:00+09:00",
        "source_sha256": checker.source_fingerprint(root),
        "model": result["meta"]["model"],
        "prompt_set": "v2",
        "runs": [{"answer_id": answer.id, "problem_id": problem.id, "mode": "free", **descriptors}],
    }
    evidence = root / "evidence.json"
    write_json(evidence, manifest)
    return checker, root, evidence


def test_live_release_gate_rejects_missing_and_demo_evidence(tmp_path: Path) -> None:
    checker = _release_checker()
    with pytest.raises(checker.ReleaseCheckError, match="missing"):
        checker.check_live_evidence(tmp_path, tmp_path / "absent-evidence.json")
    checker, root, evidence = _deliberately_invalid_live_manifest(tmp_path)
    manifest = read_json(evidence)
    manifest["is_demo"] = True
    write_json(evidence, manifest, overwrite=True)
    with pytest.raises(checker.ReleaseCheckError, match="demo"):
        checker.check_live_evidence(root, evidence)


def test_live_release_gate_uses_answer_body_then_rejects_missing_api_stages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import nonsul_review.llm as llm

    checker, root, evidence = _deliberately_invalid_live_manifest(tmp_path)
    original_fingerprint = llm.input_fingerprint
    seen: list[str] = []

    def recording_fingerprint(problem: Problem, answer_body: str) -> str:
        assert isinstance(answer_body, str)
        seen.append(answer_body)
        return original_fingerprint(problem, answer_body)

    monkeypatch.setattr(llm, "input_fingerprint", recording_fingerprint)
    with pytest.raises(checker.ReleaseCheckError, match="no API stages"):
        checker.check_live_evidence(root, evidence)
    assert seen == [_answer().body]


def test_live_release_gate_rejects_reordered_or_missing_stages(tmp_path: Path) -> None:
    checker, root, evidence = _deliberately_invalid_live_manifest(tmp_path)
    raw_path = root / "result.raw.json"
    raw = read_json(raw_path)
    raw["stages"] = [{"stage": "free:table", "attempts": []}]
    write_json(raw_path, raw, overwrite=True)
    manifest = read_json(evidence)
    manifest["runs"][0]["raw"]["sha256"] = checker.sha256_file(raw_path)
    write_json(evidence, manifest, overwrite=True)
    with pytest.raises(checker.ReleaseCheckError, match="missing, reordered, or extra"):
        checker.check_live_evidence(root, evidence)
