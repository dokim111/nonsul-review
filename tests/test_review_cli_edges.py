"""Boundary checks for request costs, retained traces, and instructor approval."""

from __future__ import annotations

from pathlib import Path

import pytest

from nonsul_review.cli import main
from nonsul_review.io import read_json, read_yaml, write_json, write_yaml
from nonsul_review.llm import PipelineError
from nonsul_review.schema import Result

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "ex-001"


def _run_args(out: Path) -> list[str]:
    return [
        "run",
        "--problem",
        str(EXAMPLE / "problem.yaml"),
        "--answer",
        str(EXAMPLE / "answers" / "a01.md"),
        "--mode",
        "rubric",
        "--out",
        str(out),
        "--demo",
    ]


def _batch_args(out: Path) -> list[str]:
    return [
        "batch",
        "--problems",
        str(EXAMPLE),
        "--answers",
        str(EXAMPLE / "answers"),
        "--mode",
        "rubric",
        "--out",
        str(out),
        "--demo",
    ]


@pytest.mark.parametrize("command", ["run", "batch"])
@pytest.mark.parametrize("blocked_ancestor", [False, True])
def test_invalid_output_directory_is_rejected_before_any_model_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str, blocked_ancestor: bool
) -> None:
    blocker = tmp_path / "existing-file"
    blocker.write_text("Preserve this unrelated file.", encoding="utf-8")
    out = blocker / "nested-output" if blocked_ancestor else blocker
    calls = []

    def should_not_run(*args, **kwargs):
        calls.append(True)
        raise PipelineError("No network request was made by this test.", {"status": "failed"})

    monkeypatch.setattr("nonsul_review.llm.run_review", should_not_run)
    args = _run_args(out) if command == "run" else _batch_args(out)
    assert main(args) == 2
    assert calls == [], "Output destinations must be usable before paying for model work."
    assert blocker.read_text(encoding="utf-8") == "Preserve this unrelated file."


def test_post_call_context_rejection_preserves_returned_raw_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    private_quote = "PRIVATE_INVALID_QUOTATION_NOT_PRESENT_IN_THE_ANSWER"

    def mismatched_result(problem, answer, mode, config):
        result = Result.model_validate(
            {
                "answer_id": answer.id,
                "problem_id": problem.id,
                "mode": mode,
                "items": [
                    {
                        "rubric_id": item.id,
                        "verdict": "met",
                        "evidence": private_quote,
                        "reason": "Provider fixture produced an incorrect quotation.",
                        "feedback": "Check the quoted text.",
                        "needs_review": False,
                    }
                    for item in problem.rubric
                ],
                "overall_feedback": "Context validation must reject these quotations.",
                "meta": {
                    "model": "injected-provider-fixture",
                    "prompt_version": "rubric-v1",
                    "temperature": 1,
                    "created_at": "2026-09-29T08:00:00+09:00",
                },
            }
        )
        return result, {"status": "succeeded", "stages": [{"response": "retained response"}]}

    monkeypatch.setattr("nonsul_review.llm.run_review", mismatched_result)
    assert main(_run_args(tmp_path)) == 1
    output = capsys.readouterr()
    assert "Traceback" not in output.err
    assert private_quote not in output.err
    raw = read_json(tmp_path / "ex-001-a01.rubric.raw.json")
    assert raw["status"] == "failed"
    assert raw["stages"] == [{"response": "retained response"}]
    assert read_json(tmp_path / "ex-001-a01.rubric.failure.json")["status"] == "failed"
    assert not (tmp_path / "ex-001-a01.rubric.json").exists()


def test_batch_keeps_successes_and_continues_after_a_middle_provider_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nonsul_review import llm

    real_run_review = llm.run_review
    called = []

    def fail_middle(problem, answer, mode, config):
        called.append(answer.id)
        if answer.id == "ex-001-a02":
            raise PipelineError(
                "Provider fixture unavailable.",
                {"status": "failed", "stages": [{"attempts": [{"valid": False}]}]},
            )
        return real_run_review(problem, answer, mode, config)

    monkeypatch.setattr(llm, "run_review", fail_middle)
    assert main(_batch_args(tmp_path)) == 1
    assert called == ["ex-001-a01", "ex-001-a02", "ex-001-a03"]
    manifest = read_json(tmp_path / "batch.rubric.json")
    assert (manifest["succeeded"], manifest["failed"]) == (2, 1)
    assert (tmp_path / "ex-001-a01.rubric.json").exists()
    assert (tmp_path / "ex-001-a03.rubric.json").exists()
    assert read_json(tmp_path / "ex-001-a02.rubric.raw.json")["stages"]
    assert read_json(tmp_path / "ex-001-a02.rubric.failure.json")["status"] == "failed"
    assert not (tmp_path / "ex-001-a02.rubric.json").exists()


def test_explicit_finalize_clears_attention_flag_for_a_determinate_draft(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(_run_args(tmp_path)) == 0
    original_path = tmp_path / "ex-001-a01.rubric.json"
    original = read_json(original_path)
    original["items"][0]["needs_review"] = True
    write_json(original_path, original, overwrite=True)
    original_bytes = original_path.read_bytes()
    review_path = tmp_path / "강사 검수.yaml"
    assert main(["review", "--result", str(original_path), "--out", str(review_path)]) == 0
    review = read_yaml(review_path)
    assert review["items"][0]["verdict"] is not None
    assert review["items"][0]["model_needs_review"] is True

    # Review flags cannot be injected as a second editable source of truth.
    review["items"][0]["needs_review"] = True
    write_yaml(review_path, review, overwrite=True)
    final_out = tmp_path / "확정 결과"
    assert main(["finalize", "--review", str(review_path), "--out", str(final_out)]) == 2
    assert not final_out.exists()
    review["items"][0].pop("needs_review")
    write_yaml(review_path, review, overwrite=True)

    assert main(["finalize", "--review", str(review_path), "--out", str(final_out)]) == 0
    final = read_json(final_out / "ex-001-a01.rubric.final.json")
    changes = read_json(final_out / "ex-001-a01.rubric.changes.json")
    assert all(item["needs_review"] is False for item in final["items"])
    assert changes["items"][0]["changes"]["needs_review"] == {"before": True, "after": False}
    assert original_path.read_bytes() == original_bytes
    assert "Traceback" not in capsys.readouterr().err


def test_finalize_unresolved_verdict_returns_actionable_nontraceback_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(_run_args(tmp_path)) == 0
    original_path = tmp_path / "ex-001-a01.rubric.json"
    original = read_json(original_path)
    original["items"][0].update(verdict=None, needs_review=True, reason="Instructor must decide.")
    write_json(original_path, original, overwrite=True)
    assert main(["review", "--result", str(original_path)]) == 0
    review_path = tmp_path / "ex-001-a01.rubric.review.yaml"
    assert main(["finalize", "--review", str(review_path)]) == 2
    error = capsys.readouterr().err
    assert "unresolved" in error
    assert "instructor" in error
    assert "Traceback" not in error
    assert not list(tmp_path.glob("*.final.json"))
    assert not list(tmp_path.glob("*.changes.json"))
