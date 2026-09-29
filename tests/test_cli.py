"""End-to-end command contracts and preflight behavior, with no paid requests."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from nonsul_review.cli import main
from nonsul_review.io import load_answer, load_problem, read_json
from nonsul_review.schema import Result, validate_result_context

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "ex-001"


def run_args(out: Path, *, mode: str = "rubric", answer: str = "a01") -> list[str]:
    return [
        "run",
        "--problem",
        str(EXAMPLE / "problem.yaml"),
        "--answer",
        str(EXAMPLE / "answers" / f"{answer}.md"),
        "--mode",
        mode,
        "--out",
        str(out),
        "--demo",
    ]


def test_help_and_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "0.1.0" in capsys.readouterr().out
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "evaluate" in capsys.readouterr().out


@pytest.mark.parametrize("mode", ["rubric", "free"])
@pytest.mark.parametrize("answer", ["a01", "a02", "a03"])
def test_demo_run_writes_valid_trace_and_same_contract(
    tmp_path: Path, mode: str, answer: str
) -> None:
    assert main(run_args(tmp_path, mode=mode, answer=answer)) == 0
    prediction = tmp_path / f"ex-001-{answer}.{mode}.json"
    result = Result.model_validate(read_json(prediction))
    validate_result_context(
        result,
        load_problem(EXAMPLE / "problem.yaml"),
        load_answer(EXAMPLE / "answers" / f"{answer}.md"),
    )
    assert result.meta["provider"] == "demo"
    assert result.meta["is_demo"] is True
    assert result.meta["model"] == "demo-fixture"
    assert (tmp_path / f"ex-001-{answer}.{mode}.raw.json").is_file()
    assert not list(tmp_path.glob("*.failure.json"))
    assert (
        main(
            [
                "validate",
                "--problem",
                str(EXAMPLE / "problem.yaml"),
                "--answer",
                str(EXAMPLE / "answers" / f"{answer}.md"),
                "--result",
                str(prediction),
            ]
        )
        == 0
    )


def test_existing_run_is_never_overwritten(tmp_path: Path) -> None:
    assert main(run_args(tmp_path)) == 0
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert main(run_args(tmp_path)) == 2
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}


def test_missing_key_is_clear_and_never_falls_back_to_demo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    args = run_args(tmp_path / "out")[:-1] + ["--model", "configured-model"]
    assert main(args) in {1, 2}
    text = capsys.readouterr()
    assert "ANTHROPIC_API_KEY" in text.err
    assert not list((tmp_path / "out").glob("ex-001-a01.rubric.json"))


def test_input_mismatch_fails_before_creating_outputs(tmp_path: Path) -> None:
    bad_answer = tmp_path / "bad.md"
    bad_answer.write_text(
        (EXAMPLE / "answers/a01.md")
        .read_text(encoding="utf-8")
        .replace("problem: ex-001", "problem: other"),
        encoding="utf-8",
    )
    args = run_args(tmp_path / "out")
    args[args.index("--answer") + 1] = str(bad_answer)
    assert main(args) == 2
    assert not (tmp_path / "out").exists()


def test_api_failure_is_saved_without_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from nonsul_review.llm import PipelineError

    def fail(*args, **kwargs):
        raise PipelineError("provider unavailable", {"attempts": [{"status": "api_error"}]})

    monkeypatch.setattr("nonsul_review.llm.run_review", fail)
    assert main(run_args(tmp_path)) == 1
    assert "Traceback" not in capsys.readouterr().err
    assert read_json(tmp_path / "ex-001-a01.rubric.failure.json")["status"] == "failed"
    assert read_json(tmp_path / "ex-001-a01.rubric.raw.json")["attempts"]
    assert not (tmp_path / "ex-001-a01.rubric.json").exists()


def batch_args(out: Path, answers: Path) -> list[str]:
    return [
        "batch",
        "--problems",
        str(EXAMPLE),
        "--answers",
        str(answers),
        "--mode",
        "rubric",
        "--out",
        str(out),
        "--demo",
    ]


def test_batch_success_summary(tmp_path: Path) -> None:
    assert main(batch_args(tmp_path, EXAMPLE / "answers")) == 0
    manifest = read_json(tmp_path / "batch.rubric.json")
    assert (manifest["selected"], manifest["succeeded"], manifest["failed"]) == (3, 3, 0)
    assert manifest["is_demo"] is True


def test_batch_entire_dataset_is_validated_before_first_call(tmp_path: Path) -> None:
    answers = tmp_path / "answers"
    shutil.copytree(EXAMPLE / "answers", answers)
    (answers / "a03.md").write_text("bad front matter", encoding="utf-8")
    output = tmp_path / "out"
    assert main(batch_args(output, answers)) == 2
    assert not output.exists()


def test_batch_split_separation(tmp_path: Path) -> None:
    answers = tmp_path / "answers"
    shutil.copytree(EXAMPLE / "answers", answers)
    second = answers / "a02.md"
    second.write_text(
        second.read_text(encoding="utf-8").replace("split: dev", "split: test"), encoding="utf-8"
    )
    output = tmp_path / "out"
    assert main(batch_args(output, answers)) == 2
    assert not output.exists()
    assert main(batch_args(output, answers) + ["--split", "test"]) == 0
    assert read_json(output / "batch.rubric.json")["selected"] == 1


def test_review_and_finalize_from_cli(tmp_path: Path) -> None:
    assert main(run_args(tmp_path)) == 0
    prediction = tmp_path / "ex-001-a01.rubric.json"
    review = tmp_path / "instructor.yaml"
    assert main(["review", "--result", str(prediction), "--out", str(review)]) == 0
    assert main(["finalize", "--review", str(review)]) == 0
    assert (tmp_path / "ex-001-a01.rubric.final.json").is_file()
    assert (tmp_path / "ex-001-a01.rubric.changes.json").is_file()


def test_invalid_sampling_settings_do_not_start_a_run(tmp_path: Path) -> None:
    assert main(run_args(tmp_path / "out") + ["--temperature", "nan"]) == 2
    assert not (tmp_path / "out").exists()


def test_eval_demo_requires_explicit_flag_and_can_write_report(tmp_path: Path) -> None:
    assert main(batch_args(tmp_path / "pred", EXAMPLE / "answers")) == 0
    args = [
        "evaluate",
        "--pred",
        str(tmp_path / "pred"),
        "--raters",
        str(EXAMPLE / "evaluation/raters"),
        "--gold",
        str(EXAMPLE / "evaluation/gold"),
        "--answers",
        str(EXAMPLE / "answers"),
        "--out",
        str(tmp_path / "report.json"),
    ]
    assert main(args) == 2
    assert not (tmp_path / "report.json").exists()
    assert main(args + ["--allow-demo"]) == 0
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report
