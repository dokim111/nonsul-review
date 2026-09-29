"""One CLI for generation, human review and reproducible evaluation."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from . import __version__
from .io import InputError, load_answer, load_problem, read_json, schema_error, write_json
from .llm import PipelineError
from .schema import Answer, Problem, Result, validate_result_context


def _generation_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--mode", choices=("rubric", "free"), required=True)
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output directory; existing files are never overwritten",
    )
    parser.add_argument("--model", help="Anthropic model ID (or NONSUL_MODEL environment variable)")
    parser.add_argument(
        "--temperature",
        type=float,
        default=1.0,
        help="Explicit sampling setting (default: 1; some models reject other values)",
    )
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument(
        "--timeout", type=float, default=60.0, help="Timeout per API request, in seconds"
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Offline fixtures for bundled examples only; NOT live LLM output",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nonsul-review",
        description="수리논술 강사용 판정 초안·검수·평가 도구. 최종 판정은 강사가 확정합니다.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="답안 하나의 판정 초안 생성")
    run.add_argument("--problem", type=Path, required=True)
    run.add_argument("--answer", type=Path, required=True)
    _generation_options(run)

    batch = commands.add_parser("batch", help="입력 전체 검증 후 여러 답안을 순서대로 처리")
    batch.add_argument("--problems", type=Path, required=True)
    batch.add_argument("--answers", type=Path, required=True)
    batch.add_argument(
        "--split",
        choices=("dev", "test"),
        help="Select a dataset split; every answer must declare split",
    )
    _generation_options(batch)

    review = commands.add_parser("review", help="강사가 편집할 검수 YAML 생성")
    review.add_argument("--result", type=Path, required=True)
    review.add_argument("--out", type=Path, help="Review YAML path")
    finalize = commands.add_parser("finalize", help="강사의 확정본과 변경 기록 저장")
    finalize.add_argument("--review", type=Path, required=True)
    finalize.add_argument("--out", type=Path, help="Final output directory")

    evaluate = commands.add_parser(
        "evaluate", help="최초 채점 일치도와 기준 판정 대비 오류 탐지 계산"
    )
    evaluate.add_argument("--pred", type=Path, required=True)
    evaluate.add_argument("--raters", type=Path, required=True)
    evaluate.add_argument("--gold", type=Path, required=True)
    evaluate.add_argument(
        "--answers", type=Path, help="Original answer metadata for error-type slices"
    )
    evaluate.add_argument("--out", type=Path, help="JSON report path (default: stdout)")
    evaluate.add_argument(
        "--allow-demo",
        action="store_true",
        help="Allow fixture results; labels report as a demonstration",
    )

    validate = commands.add_parser(
        "validate", help="API 호출 없이 문항·답안·판정표 형식과 연결 확인"
    )
    validate.add_argument("--problem", type=Path, required=True)
    validate.add_argument("--answer", type=Path)
    validate.add_argument("--result", type=Path)
    return parser


def _configuration(args: argparse.Namespace) -> Any:
    from .llm import RunConfig

    return RunConfig(
        model=args.model,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        timeout=args.timeout,
        demo=args.demo,
    )


def _paths(out: Path, answer_id: str, mode: str) -> dict[str, Path]:
    stem = f"{answer_id}.{mode}"
    return {
        "result": out / f"{stem}.json",
        "raw": out / f"{stem}.raw.json",
        "failure": out / f"{stem}.failure.json",
    }


def _ensure_new(paths: list[Path]) -> None:
    for path in paths:
        if path.exists() or path.is_symlink():
            raise FileExistsError(f"output already exists: {path}; choose a new --out")


def _ensure_output_directory(directory: Path) -> None:
    """Detect unusable destinations before making potentially billed API calls."""
    directory.mkdir(parents=True, exist_ok=True)
    if not directory.is_dir():
        raise InputError(f"--out must be a directory: {directory}")
    with tempfile.TemporaryFile(dir=directory) as probe:
        probe.write(b"nonsul-review output preflight")
        probe.flush()


def _run_one(problem: Problem, answer: Answer, args: argparse.Namespace, config: Any) -> dict:
    from .llm import run_review

    if answer.problem != problem.id:
        raise InputError("answer.problem does not match problem.id")
    paths = _paths(args.out, answer.id, args.mode)
    _ensure_new(list(paths.values()))
    _ensure_output_directory(args.out)
    try:
        result, raw = run_review(problem, answer, args.mode, config)
        try:
            validate_result_context(result, problem, answer)
        except (ValueError, ValidationError):
            raw["status"] = "failed"
            raw["failure"] = "context_validation_failed"
            raise PipelineError("Generated result failed input/context validation.", raw) from None
    except PipelineError as exc:
        write_json(paths["raw"], exc.raw)
        failure = {
            "kind": "run_failure",
            "answer_id": answer.id,
            "problem_id": problem.id,
            "mode": args.mode,
            "status": "failed",
            "error": str(exc),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "raw_file": paths["raw"].name,
        }
        write_json(paths["failure"], failure)
        raise
    write_json(paths["raw"], raw)
    write_json(paths["result"], result.model_dump(mode="json"))
    return {
        "answer_id": answer.id,
        "problem_id": problem.id,
        "mode": args.mode,
        "status": "succeeded",
        "result": paths["result"].name,
        "raw": paths["raw"].name,
    }


def _directory(path: Path, label: str) -> Path:
    if not path.is_dir():
        raise InputError(f"{label} must be an existing directory: {path}")
    return path


def _batch_inputs(args: argparse.Namespace) -> tuple[dict[str, Problem], list[Answer], int]:
    problems_dir = _directory(args.problems, "--problems")
    answers_dir = _directory(args.answers, "--answers")
    # Support flat problem directories and examples/<id>/problem.yaml layouts.
    problem_paths = set(problems_dir.glob("*.yaml")) | set(problems_dir.glob("*.yml"))
    problem_paths |= set(problems_dir.rglob("problem.yaml")) | set(
        problems_dir.rglob("problem.yml")
    )
    if not problem_paths:
        raise InputError("no problem YAML files found")
    problems: dict[str, Problem] = {}
    for path in sorted(problem_paths):
        problem = load_problem(path)
        if problem.id in problems:
            raise InputError(f"duplicate problem ID: {problem.id}")
        problems[problem.id] = problem

    paths = sorted(answers_dir.rglob("*.md"))
    if not paths:
        raise InputError("no Markdown answer files found")
    answers = [load_answer(path) for path in paths]
    identifiers = [answer.id for answer in answers]
    if len(set(identifiers)) != len(identifiers):
        raise InputError("duplicate answer IDs in batch")
    for answer in answers:
        if answer.problem not in problems:
            raise InputError(f"no problem for answer: {answer.id}")
    total = len(answers)
    if args.split:
        if any(answer.split is None for answer in answers):
            raise InputError("--split requires split metadata in every answer file")
        answers = [answer for answer in answers if answer.split == args.split]
        if not answers:
            raise InputError("no answers match --split")
    elif len({answer.split for answer in answers if answer.split}) > 1:
        raise InputError("dev and test answers must be run separately; select --split")
    _ensure_new([args.out / f"batch.{args.mode}.json"])
    for answer in answers:
        _ensure_new(list(_paths(args.out, answer.id, args.mode).values()))
    return problems, answers, total


def _batch(args: argparse.Namespace) -> int:
    problems, answers, total = _batch_inputs(args)
    config = _configuration(args)
    _ensure_output_directory(args.out)
    entries = []
    for index, answer in enumerate(answers, 1):
        print(f"[{index}/{len(answers)}] {answer.id}", file=sys.stderr)
        try:
            entries.append(_run_one(problems[answer.problem], answer, args, config))
        except PipelineError as exc:
            entries.append(
                {
                    "answer_id": answer.id,
                    "problem_id": answer.problem,
                    "mode": args.mode,
                    "status": "failed",
                    "error": str(exc),
                }
            )
    failed = sum(entry["status"] == "failed" for entry in entries)
    manifest = {
        "kind": "batch_manifest",
        "version": __version__,
        "mode": args.mode,
        "split": args.split,
        "is_demo": args.demo,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "total_input_answers": total,
        "selected": len(answers),
        "succeeded": len(answers) - failed,
        "failed": failed,
        "runs": entries,
    }
    destination = args.out / f"batch.{args.mode}.json"
    write_json(destination, manifest)
    print(f"{destination} ({len(answers) - failed} succeeded, {failed} failed)")
    return 1 if failed else 0


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "run":
        problem, answer = load_problem(args.problem), load_answer(args.answer)
        record = _run_one(problem, answer, args, _configuration(args))
        print(args.out / record["result"])
        if args.demo:
            print("DEMO: 저장된 출력은 수작업 예시이며 실제 LLM 결과가 아닙니다.", file=sys.stderr)
        return 0
    if args.command == "batch":
        return _batch(args)
    if args.command == "review":
        from .review import export_review

        print(export_review(args.result, args.out))
        return 0
    if args.command == "finalize":
        from .review import finalize_review

        for path in finalize_review(args.review, args.out):
            print(path)
        return 0
    if args.command == "evaluate":
        from .evaluate import evaluate_directories

        if args.out:
            _ensure_new([args.out])
        report = evaluate_directories(
            args.pred, args.raters, args.gold, args.answers, allow_demo=args.allow_demo
        )
        if args.out:
            write_json(args.out, report)
            print(args.out)
        else:
            print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    if args.command == "validate":
        problem = load_problem(args.problem)
        if args.answer:
            answer = load_answer(args.answer)
            if answer.problem != problem.id:
                raise InputError("answer.problem does not match problem.id")
            if args.result:
                result = Result.model_validate(read_json(args.result))
                validate_result_context(result, problem, answer)
        elif args.result:
            raise InputError("--result requires --answer")
        print("VALID: 입력 형식·ID·근거 인용을 확인했습니다.")
        return 0
    raise InputError("unknown command")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _dispatch(args)
    except ValidationError as exc:
        print(f"ERROR: {schema_error(exc)}", file=sys.stderr)
        return 2
    except PipelineError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except (ValueError, OSError) as exc:
        # Public module exceptions have sanitized messages, not input contents.
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted; completed outputs are preserved.", file=sys.stderr)
        return 130
