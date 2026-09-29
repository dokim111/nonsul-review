"""The v2 procedure: prompt selection, review flags and their downstream handling."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import yaml
from test_llm import FakeClient, item, response

from nonsul_review.evaluate import _paired_modes, _PredictionGroup
from nonsul_review.io import read_json, write_json
from nonsul_review.llm import (
    PROMPT_SETS,
    PipelineError,
    RunConfig,
    prompt_file_names,
    run_review,
)
from nonsul_review.review import ReviewError, export_review, finalize_review
from nonsul_review.schema import Answer, Problem, Result, result_to_json

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def inputs(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NONSUL_MODEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    problem = Problem.model_validate(
        {
            "id": "test-problem",
            "title": "자체 작성 테스트",
            "prompt": "관계식과 결론을 제시하시오.",
            "model_answer": "x=1을 얻고 y=2를 구한다.",
            "rubric": [
                {"id": "R1", "step": "관계식", "criteria": "x=1 유도"},
                {"id": "R2", "step": "결론", "criteria": "y=2 유도"},
            ],
        }
    )
    answer = Answer(
        id="v2-answer",
        problem=problem.id,
        source="synthetic",
        body="조건에 의해 x=1이다. 따라서 y=2이다.",
    )
    return problem, answer


def _config(prompt_set: str = "v2") -> RunConfig:
    return RunConfig(model="unit-test-model", prompt_set=prompt_set, max_retries=0)


def _flag(rubric_id: str = "R2", note: str = "종합 단계에서 R2의 근거가 부족하다고 판단했다."):
    return {"rubric_id": rubric_id, "note": note}


def test_v2_rubric_flags_mark_items_without_changing_judgements(inputs):
    problem, answer = inputs
    client = FakeClient(
        [
            response(item()),
            response(item("R2", "y=2")),
            response({"overall_feedback": "잘 정리했습니다.", "review_flags": [_flag()]}),
        ]
    )
    result, raw = run_review(problem, answer, "rubric", _config(), client=client)
    assert [flag.rubric_id for flag in result.review_flags] == ["R2"]
    by_id = {entry.rubric_id: entry for entry in result.items}
    assert by_id["R2"].needs_review is True
    assert by_id["R1"].needs_review is False
    # The item-stage verdict and reason are preserved; the note stays separate.
    assert by_id["R2"].verdict == "met"
    assert by_id["R2"].reason == "답안에서 근거를 확인했다."
    assert result.meta["prompt_version"] == raw["prompt_version"] == "rubric-v2"
    assert set(result.meta["prompt_sha256"]) == prompt_file_names("rubric", "v2")
    first_system = client.calls[0]["system"]
    assert first_system.startswith(
        (ROOT / "src/nonsul_review/prompts/common-v2.txt").read_text(encoding="utf-8")
    )
    assert result_to_json(result)["review_flags"] == [_flag()]


def test_v2_free_table_stage_reports_flags(inputs):
    problem, answer = inputs
    client = FakeClient(
        [
            response({"overall_feedback": "전체 피드백입니다."}),
            response(
                {
                    "items": [item(), item("R2", "y=2")],
                    "review_flags": [_flag("R1", "앞선 전체 피드백의 부호 설명이 틀렸다.")],
                }
            ),
        ]
    )
    result, _ = run_review(problem, answer, "free", _config(), client=client)
    assert result.meta["prompt_version"] == "free-v2"
    assert result.items[0].needs_review is True
    assert result.review_flags[0].note.startswith("앞선 전체 피드백")
    # The earlier holistic feedback is kept verbatim, not rewritten.
    assert result.overall_feedback == "전체 피드백입니다."


@pytest.mark.parametrize(
    "final",
    [
        {"overall_feedback": "필드 누락"},
        {"overall_feedback": "없는 ID", "review_flags": [_flag("R9")]},
        {"overall_feedback": "빈 메모", "review_flags": [_flag("R1", "   ")]},
        {"overall_feedback": "추가 키", "review_flags": [], "score": 3},
    ],
)
def test_v2_final_stage_flags_are_strictly_validated(inputs, final):
    problem, answer = inputs
    client = FakeClient([response(item()), response(item("R2", "y=2")), response(final)])
    with pytest.raises(PipelineError) as caught:
        run_review(problem, answer, "rubric", _config(), client=client)
    assert caught.value.raw["stages"][-1]["attempts"][-1]["valid"] is False


def test_duplicate_flags_for_one_item_are_preserved(inputs):
    problem, answer = inputs
    flags = [_flag("R1", "첫째 메모다."), _flag("R1", "둘째 메모다.")]
    client = FakeClient(
        [
            response(item()),
            response(item("R2", "y=2")),
            response({"overall_feedback": "정리했습니다.", "review_flags": flags}),
        ]
    )
    result, _ = run_review(problem, answer, "rubric", _config(), client=client)
    assert [flag.note for flag in result.review_flags] == ["첫째 메모다.", "둘째 메모다."]


def test_v1_remains_the_library_default_and_keeps_its_record_shape(inputs):
    problem, answer = inputs
    client = FakeClient(
        [response(item()), response(item("R2", "y=2")), response({"overall_feedback": "v1"})]
    )
    result, _ = run_review(
        problem, answer, "rubric", RunConfig(model="unit-test-model"), client=client
    )
    assert result.meta["prompt_version"] == "rubric-v1"
    assert set(result.meta["prompt_sha256"]) == prompt_file_names("rubric", "v1")
    assert "review_flags" not in result_to_json(result)


def test_prompt_set_is_validated():
    with pytest.raises(ValueError, match="prompt_set"):
        RunConfig(prompt_set="v9")
    for prompt_set, files in PROMPT_SETS.items():
        for name in prompt_file_names("rubric", prompt_set) | prompt_file_names("free", prompt_set):
            assert (ROOT / "src/nonsul_review/prompts" / name).is_file(), (prompt_set, name)
        assert files["review_flags"] is (prompt_set != "v1")


def test_demo_fixtures_run_under_v2(monkeypatch, tmp_path):
    from nonsul_review.io import load_answer, load_problem

    monkeypatch.chdir(tmp_path)
    problem = load_problem(ROOT / "examples/ex-001/problem.yaml")
    answer = load_answer(ROOT / "examples/ex-001/answers/a02.md")
    for mode in ("rubric", "free"):
        result, _ = run_review(problem, answer, mode, RunConfig(demo=True, prompt_set="v2"))
        assert result.review_flags == []
        assert result.meta["prompt_version"] == f"{mode}-v2"


def test_result_schema_rejects_flags_for_unknown_items():
    data = read_json(ROOT / "examples/ex-001/results/ex-001-a01.rubric.json")
    data["review_flags"] = [_flag("R9")]
    with pytest.raises(ValueError):
        Result.model_validate(data)


def _flagged_result_file(tmp_path: Path) -> Path:
    data = read_json(ROOT / "examples/ex-001/results/ex-001-a02.rubric.json")
    data["meta"]["prompt_version"] = "rubric-v2"
    data["review_flags"] = [_flag("R1", "종합 단계에서 확인이 필요하다.")]
    data["items"][0]["needs_review"] = True
    path = tmp_path / "ex-001-a02.rubric.json"
    write_json(path, data)
    return path


def test_review_copy_shows_flags_and_finalize_preserves_them(tmp_path):
    source = _flagged_result_file(tmp_path)
    review_path = export_review(source)
    review = yaml.safe_load(review_path.read_text(encoding="utf-8"))
    notes = {entry["rubric_id"]: entry["model_review_flags"] for entry in review["items"]}
    assert notes["R1"] == ["종합 단계에서 확인이 필요하다."]
    assert all(notes[key] == [] for key in notes if key != "R1")
    final_path, changes_path = finalize_review(review_path, tmp_path / "final")
    final = read_json(final_path)
    changes = read_json(changes_path)
    assert final["review_flags"] == [_flag("R1", "종합 단계에서 확인이 필요하다.")]
    assert all(entry["needs_review"] is False for entry in final["items"])
    assert changes["summary"]["model_review_flag_count"] == 1
    assert changes["items"][0]["model_review_flags"] == ["종합 단계에서 확인이 필요하다."]


@pytest.mark.parametrize("tamper", ["edit", "remove"])
def test_review_notes_are_read_only(tmp_path, tamper):
    review_path = export_review(_flagged_result_file(tmp_path))
    review = yaml.safe_load(review_path.read_text(encoding="utf-8"))
    for entry in review["items"]:
        if entry["rubric_id"] == "R1":
            if tamper == "edit":
                entry["model_review_flags"] = ["지운 메모"]
            else:
                del entry["model_review_flags"]
    review_path.write_text(yaml.safe_dump(review, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ReviewError, match="review notes"):
        finalize_review(review_path, tmp_path / "final")


def test_v1_results_still_review_and_finalize_without_flag_fields(tmp_path):
    data = read_json(ROOT / "examples/ex-001/results/ex-001-a01.rubric.json")
    source = tmp_path / "ex-001-a01.rubric.json"
    write_json(source, data)
    final_path, _ = finalize_review(export_review(source), tmp_path / "final")
    assert "review_flags" not in read_json(final_path)


def _group(mode: str, prompt_set: str, common_hash: str = "c" * 64) -> _PredictionGroup:
    return _PredictionGroup(
        settings={
            "mode": mode,
            "prompt_version": f"{mode}-{prompt_set}",
            "prompt_sha256": {f"common-{prompt_set}.txt": common_hash},
            "response_models": ["m"],
            "config_sha256": "f" * 64,
            "model": "m",
        },
        answers={"a1": "a1"},
        items={},
        input_hashes={"a1": "i" * 64},
        answer_body_hashes={"a1": "b" * 64},
        group_id=f"{mode}-{prompt_set}",
    )


def test_paired_modes_require_the_same_prompt_set():
    same = _paired_modes([_group("rubric", "v2"), _group("free", "v2")])
    assert same[0]["same_common_prompt_verified"] is True
    assert same[0]["comparable_on_shared_answers"] is True
    mixed = _paired_modes([_group("rubric", "v1"), _group("free", "v2")])
    assert mixed[0]["same_common_prompt_verified"] is False
    assert mixed[0]["comparable_on_shared_answers"] is False


def _release_checker():
    spec = importlib.util.spec_from_file_location(
        "release_check", ROOT / "scripts/release_check.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _stage_with_final_text(value: dict) -> list[dict]:
    return [
        {"attempts": [{"response": {"content": [{"type": "text", "text": json.dumps(value)}]}}]}
    ]


def test_release_gate_checks_flags_against_the_final_response(inputs):
    checker = _release_checker()
    problem, answer = inputs
    client = FakeClient(
        [
            response(item()),
            response(item("R2", "y=2")),
            response({"overall_feedback": "정리했습니다.", "review_flags": [_flag()]}),
        ]
    )
    result, _ = run_review(problem, answer, "rubric", _config(), client=client)
    checker._check_review_flags(
        result, _stage_with_final_text({"review_flags": [_flag()]}), supported=True
    )
    with pytest.raises(checker.ReleaseCheckError, match="differ"):
        checker._check_review_flags(
            result, _stage_with_final_text({"review_flags": []}), supported=True
        )
    unmarked = result.model_copy(
        update={
            "items": [entry.model_copy(update={"needs_review": False}) for entry in result.items]
        }
    )
    with pytest.raises(checker.ReleaseCheckError, match="needs_review"):
        checker._check_review_flags(
            unmarked, _stage_with_final_text({"review_flags": [_flag()]}), supported=True
        )
    assert checker.REQUIRED_PROMPT_SET == "v2"


def test_answer_and_problem_models_unchanged_by_v2():
    # Revision metadata lives in a separate ledger, never in model inputs.
    assert "revision_of" not in Answer.model_fields
    assert "revision" not in Problem.model_fields


@pytest.mark.parametrize(("flag", "expected"), [(None, "v2"), ("v1", "v1")])
def test_cli_defaults_to_v2_and_can_reproduce_v1(tmp_path, monkeypatch, flag, expected):
    from nonsul_review.cli import main

    monkeypatch.chdir(tmp_path)
    argv = [
        "run",
        "--problem",
        str(ROOT / "examples/ex-001/problem.yaml"),
        "--answer",
        str(ROOT / "examples/ex-001/answers/a01.md"),
        "--mode",
        "free",
        "--demo",
        "--out",
        str(tmp_path / "out"),
    ]
    if flag:
        argv += ["--prompt-set", flag]
    assert main(argv) == 0
    data = read_json(tmp_path / "out/ex-001-a01.free.json")
    assert data["meta"]["prompt_version"] == f"free-{expected}"
    assert ("review_flags" in data) is (expected == "v2")


@pytest.mark.parametrize(
    "table",
    [
        {"items": [item(), item("R2", "y=2")]},
        {"items": [item(), item("R2", "y=2")], "review_flags": None},
    ],
)
def test_v2_free_table_missing_flags_key_is_not_filled_in(inputs, table):
    problem, answer = inputs
    client = FakeClient([response({"overall_feedback": "전체 피드백입니다."}), response(table)])
    with pytest.raises(PipelineError):
        run_review(problem, answer, "free", _config(), client=client)


def test_mixed_procedure_versions_are_reported_not_paired():
    from nonsul_review.evaluate import evaluate_directories

    assert "mix procedure versions" in (ROOT / "src/nonsul_review/evaluate.py").read_text(
        encoding="utf-8"
    )
    pairs = _paired_modes([_group("rubric", "v1"), _group("free", "v2"), _group("free", "v1")])
    comparable = [pair for pair in pairs if pair["comparable_on_shared_answers"]]
    assert [(pair["rubric_group_id"], pair["free_group_id"]) for pair in comparable] == [
        ("rubric-v1", "free-v1")
    ]
    assert callable(evaluate_directories)
