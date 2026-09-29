#!/usr/bin/env python3
"""Inspect public files/distributions and optionally require genuine live examples.

This is a release hygiene check, not proof of API provenance or a general PII scanner.
Never print matching secret values. No network access or paid API calls occur here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tarfile
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE = Path("examples/ex-001/live/evidence.json")
# Public live evidence must come from the current review procedure.
REQUIRED_PROMPT_SET = "v2"
PUBLIC_ROOT_FILES = {
    "README.md",
    "LICENSE",
    "pyproject.toml",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "MANIFEST.in",
    ".env.example",
    ".gitignore",
}
PUBLIC_DIRS = {"src", "tests", "scripts", "docs", "examples", ".github"}
FORBIDDEN_PARTS = {".git", ".venv", "venv", "__pycache__", "private"}
SECRET_PATTERNS = (
    re.compile(rb"sk-ant-[A-Za-z0-9_-]{20,}"),
    re.compile(rb"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(rb"github_pat_[A-Za-z0-9_]{30,}"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
)
MAX_PUBLIC_FILE_BYTES = 16 * 1024 * 1024


class ReleaseCheckError(ValueError):
    """A public artifact failed a release gate."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_fingerprint(root: Path) -> str:
    """Hash source, prompt resources, and package metadata, independent of Git state."""
    paths = [root / "pyproject.toml"]
    paths.extend(
        p
        for p in (root / "src/nonsul_review").rglob("*")
        if p.is_file() and p.suffix in {".py", ".txt", ".json"} and "__pycache__" not in p.parts
    )
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda p: p.relative_to(root).as_posix()):
        if path.is_symlink():
            raise ReleaseCheckError("source fingerprint cannot include symlinks")
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _safe_name(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name or "\0" in name:
        raise ReleaseCheckError("archive or manifest has an unsafe path")
    return path


def check_name(name: str) -> None:
    path = _safe_name(name)
    if any(part in FORBIDDEN_PARTS for part in path.parts):
        raise ReleaseCheckError(f"forbidden public path: {name}")
    if path.name.startswith(".env") and path.name != ".env.example":
        raise ReleaseCheckError(f"local environment file included: {name}")
    if path.suffix.lower() in {".pem", ".key", ".pyc", ".pyo"}:
        raise ReleaseCheckError(f"private key or cache file included: {name}")
    if path.name.lower().startswith("credentials") and path.suffix.lower() == ".json":
        raise ReleaseCheckError(f"credential file included: {name}")


def check_bytes(name: str, content: bytes) -> None:
    if len(content) > MAX_PUBLIC_FILE_BYTES:
        raise ReleaseCheckError(f"unexpectedly large public file: {name}")
    if any(pattern.search(content) for pattern in SECRET_PATTERNS):
        raise ReleaseCheckError(f"possible credential in {name}; value withheld")


def _is_repository_root(root: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return False
    return result.returncode == 0 and Path(result.stdout.strip()).resolve() == root.resolve()


def public_files(root: Path) -> list[Path]:
    """Include tracked files even if .gitignore would now exclude them."""
    if _is_repository_root(root):
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=root,
            capture_output=True,
            check=True,
        )
        names = {name.decode("utf-8") for name in result.stdout.split(b"\0") if name}
        return [
            root / name
            for name in sorted(names)
            if (root / name).exists() or (root / name).is_symlink()
        ]
    paths = [root / name for name in PUBLIC_ROOT_FILES if (root / name).is_file()]
    for dirname in PUBLIC_DIRS:
        paths.extend(
            p
            for p in (root / dirname).rglob("*")
            if p.is_file()
            and not any(
                part in {"__pycache__", ".pytest_cache", ".ruff_cache"}
                for part in p.relative_to(root).parts
            )
            and not any(part.endswith(".egg-info") for part in p.parts)
            and "live" not in p.relative_to(root).parts
        )
    return sorted(set(paths))


def check_sources(root: Path) -> int:
    paths = public_files(root)
    for path in paths:
        name = path.relative_to(root).as_posix()
        check_name(name)
        if path.is_symlink():
            raise ReleaseCheckError(f"public source symlink requires review: {name}")
        if path.stat().st_size > MAX_PUBLIC_FILE_BYTES:
            raise ReleaseCheckError(f"unexpectedly large public file: {name}")
        check_bytes(name, path.read_bytes())
    return len(paths)


def check_distributions(dist: Path) -> list[str]:
    wheels = sorted(dist.glob("*.whl"))
    sdists = sorted(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ReleaseCheckError("dist must contain exactly one wheel and one source tarball")
    for path in dist.iterdir():
        if path not in [*wheels, *sdists]:
            raise ReleaseCheckError("dist contains an unexpected file; use a clean build directory")
    for archive in [*wheels, *sdists]:
        names: list[str] = []
        if archive.suffix == ".whl":
            with zipfile.ZipFile(archive) as bundle:
                for member in bundle.infolist():
                    if member.is_dir():
                        continue
                    check_name(member.filename)
                    if member.file_size > MAX_PUBLIC_FILE_BYTES:
                        raise ReleaseCheckError("oversized distribution member")
                    # Unix file-mode bits may encode a symlink inside a ZIP.
                    if (member.external_attr >> 16) & 0o170000 == 0o120000:
                        raise ReleaseCheckError("distribution contains a symlink")
                    check_bytes(member.filename, bundle.read(member))
                    names.append(member.filename)
        else:
            with tarfile.open(archive, "r:gz") as bundle:
                for member in bundle:
                    check_name(member.name)
                    if member.isdir():
                        continue
                    if not member.isfile() or member.size > MAX_PUBLIC_FILE_BYTES:
                        raise ReleaseCheckError("distribution has an unsafe member")
                    stream = bundle.extractfile(member)
                    if stream is None:
                        raise ReleaseCheckError("source tarball member cannot be read")
                    check_bytes(member.name, stream.read())
                    names.append(member.name)
        if not any("/prompts/" in f and f.endswith(".txt") for f in names):
            raise ReleaseCheckError(f"packaged prompts are missing from {archive.name}")
        if not any(f.endswith("/demo/cases.json") for f in names):
            raise ReleaseCheckError(f"packaged demo cases are missing from {archive.name}")
    return [archive.name for archive in [*wheels, *sdists]]


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ReleaseCheckError("evidence JSON must be an object")
    return value


def _manifest_file(root: Path, descriptor: Any) -> Path:
    if not isinstance(descriptor, dict):
        raise ReleaseCheckError("evidence file descriptor is missing")
    name = descriptor.get("path")
    expected_hash = descriptor.get("sha256")
    if not isinstance(name, str) or not isinstance(expected_hash, str):
        raise ReleaseCheckError("evidence file descriptor is invalid")
    check_name(name)
    path = root / _safe_name(name)
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ReleaseCheckError(f"missing or unsafe evidence file: {name}")
    check_bytes(name, path.read_bytes())
    if sha256_file(path) != expected_hash:
        raise ReleaseCheckError(f"evidence file changed after the live run: {name}")
    return path


def _is_tracked(root: Path, path: Path) -> bool:
    return (
        subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", path.relative_to(root).as_posix()],
            cwd=root,
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )


def _check_review_flags(result: Any, stages: list[Any], supported: bool) -> None:
    """The final stage's flags must reach the result unchanged and mark their items."""
    flags = [flag.model_dump(mode="json") for flag in result.review_flags]
    if not supported:
        if flags:
            raise ReleaseCheckError("this procedure version does not produce review flags")
        return
    final_attempt = stages[-1]["attempts"][-1]
    texts = [
        block["text"]
        for block in final_attempt["response"]["content"]
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    try:
        reported = json.loads("\n".join(texts))["review_flags"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ReleaseCheckError("final API stage did not return review_flags") from exc
    if reported != flags:
        raise ReleaseCheckError("result review flags differ from the final API stage response")
    flagged = {flag["rubric_id"] for flag in flags}
    if any(item.rubric_id in flagged and not item.needs_review for item in result.items):
        raise ReleaseCheckError("flagged items must be marked needs_review")


def check_live_evidence(root: Path, evidence: Path, *, require_tracked: bool = False) -> int:
    """Require paired public a01/a02 examples with valid non-demo API stage logs."""
    from nonsul_review import __version__
    from nonsul_review.io import load_answer, load_problem
    from nonsul_review.llm import (
        PROMPT_SETS,
        RunConfig,
        config_fingerprint,
        input_fingerprint,
        prompt_file_names,
    )
    from nonsul_review.schema import Result, prompt_set_of, validate_result_context

    if not evidence.is_file():
        raise ReleaseCheckError(
            "live evidence is missing; run scripts/live_smoke.py with your API key and model"
        )
    manifest = _load_json(evidence)
    if (
        manifest.get("schema_version") != "1.0"
        or manifest.get("is_demo") is not False
        or manifest.get("provider") != "anthropic"
    ):
        raise ReleaseCheckError("live evidence must be genuine Anthropic output, not a demo")
    if manifest.get("prompt_set") != REQUIRED_PROMPT_SET:
        raise ReleaseCheckError(
            f"live evidence must use prompt set {REQUIRED_PROMPT_SET}; rerun scripts/live_smoke.py"
        )
    if manifest.get("tool_version") != __version__:
        raise ReleaseCheckError("live evidence was produced with a different tool version")
    if manifest.get("source_sha256") != source_fingerprint(root):
        raise ReleaseCheckError("source or prompts changed; rerun the public live examples")
    try:
        created = datetime.fromisoformat(manifest["created_at"])
        if created.tzinfo is None:
            raise ValueError("timezone missing")
    except (KeyError, TypeError, ValueError) as exc:
        raise ReleaseCheckError("live evidence needs a timestamp with timezone") from exc
    runs = manifest.get("runs")
    if not isinstance(runs, list) or not runs:
        raise ReleaseCheckError("live evidence has no runs")
    seen: set[tuple[str, str]] = set()
    tracked_files = [evidence]
    common_configs: set[str] = set()
    common_response_models: set[tuple[str, ...]] = set()
    for run in runs:
        if not isinstance(run, dict):
            raise ReleaseCheckError("live run entry is invalid")
        paths = {
            kind: _manifest_file(root, run.get(kind))
            for kind in ("problem", "answer", "result", "raw")
        }
        tracked_files.extend(paths.values())
        result = Result.model_validate(_load_json(paths["result"]))
        problem = load_problem(paths["problem"])
        answer = load_answer(paths["answer"])
        validate_result_context(result, problem, answer)
        if answer.source != "synthetic":
            raise ReleaseCheckError("public live evidence must use synthetic examples")
        if result.problem_id != "ex-001":
            raise ReleaseCheckError("live gate is limited to the bundled public example")
        meta = result.meta
        if meta.get("input_sha256") != input_fingerprint(problem, answer.body):
            raise ReleaseCheckError("live result input hash does not match its source files")
        if (
            meta.get("answer_body_sha256")
            != hashlib.sha256(answer.body.encode("utf-8")).hexdigest()
        ):
            raise ReleaseCheckError("live result answer hash does not match its source file")
        raw = _load_json(paths["raw"])
        if meta.get("is_demo") is not False or meta.get("provider") != "anthropic":
            raise ReleaseCheckError("demo result cannot satisfy the live release gate")
        if meta.get("model") != manifest.get("model"):
            raise ReleaseCheckError("live result model differs from the evidence manifest")
        key = (result.answer_id, result.mode)
        if key in seen:
            raise ReleaseCheckError("duplicate live example")
        seen.add(key)
        if (
            run.get("answer_id") != result.answer_id
            or run.get("mode") != result.mode
            or run.get("problem_id") != result.problem_id
        ):
            raise ReleaseCheckError("live result identifiers differ from the manifest")
        if (
            raw.get("provider") != "anthropic"
            or raw.get("is_demo") is not False
            or raw.get("status") != "succeeded"
            or raw.get("mode") != result.mode
            or raw.get("schema_version") != "1.0"
            or raw.get("model") != meta.get("model")
            or raw.get("prompt_version") != meta.get("prompt_version")
            or raw.get("created_at") != meta.get("created_at")
            or raw.get("input_sha256") != meta.get("input_sha256")
            or raw.get("config_sha256") != meta.get("config_sha256")
        ):
            raise ReleaseCheckError("raw API log does not match the successful result")
        config_hash = meta.get("config_sha256")
        if not isinstance(config_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", config_hash):
            raise ReleaseCheckError("live result needs a valid configuration hash")
        config = RunConfig(
            model=meta.get("model"),
            temperature=meta.get("temperature"),
            max_tokens=meta.get("max_tokens"),
            timeout=meta.get("timeout"),
            max_retries=meta.get("max_retries"),
        )
        if config_hash != config_fingerprint(config, "anthropic"):
            raise ReleaseCheckError("configuration hash does not match the recorded settings")
        common_configs.add(config_hash)
        stages = raw.get("stages")
        if not isinstance(stages, list) or not stages:
            raise ReleaseCheckError("raw log has no API stages")
        stage_names = (
            [f"rubric:{item.id}" for item in problem.rubric] + ["rubric:feedback"]
            if result.mode == "rubric"
            else ["free:feedback", "free:table"]
        )
        if [
            stage.get("stage") if isinstance(stage, dict) else None for stage in stages
        ] != stage_names:
            raise ReleaseCheckError("raw log has missing, reordered, or extra API stages")
        prompt_set = prompt_set_of(meta.get("prompt_version"))
        if prompt_set != REQUIRED_PROMPT_SET or meta.get("prompt_version") != (
            f"{result.mode}-{REQUIRED_PROMPT_SET}"
        ):
            raise ReleaseCheckError(
                f"live result must use the {REQUIRED_PROMPT_SET} procedure for its mode"
            )
        common_name = PROMPT_SETS[prompt_set]["common"]
        prompt_names = prompt_file_names(result.mode, prompt_set)
        prompts = {
            name: (root / "src/nonsul_review/prompts" / name).read_text(encoding="utf-8")
            for name in prompt_names
        }
        expected_prompt_hashes = {
            name: hashlib.sha256(value.encode("utf-8")).hexdigest()
            for name, value in prompts.items()
        }
        if (
            meta.get("prompt_sha256") != expected_prompt_hashes
            or raw.get("prompt_sha256") != expected_prompt_hashes
        ):
            raise ReleaseCheckError("live prompt hashes do not match this release's prompts")
        response_models: set[str] = set()
        for stage in stages:
            if not isinstance(stage, dict):
                raise ReleaseCheckError("raw API stage is malformed")
            attempts = stage.get("attempts")
            if not isinstance(attempts, list) or not 1 <= len(attempts) <= 3:
                raise ReleaseCheckError("raw API stage has no attempts")
            prompt_name = stage.get("prompt_file")
            if prompt_name not in prompts:
                raise ReleaseCheckError("API stage references an unknown prompt")
            prompt_text = prompts[common_name] + "\n\n" + prompts[prompt_name]
            if (
                stage.get("prompt_sha256")
                != hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
            ):
                raise ReleaseCheckError("API stage prompt does not match the current prompt")
            for index, attempt in enumerate(attempts, start=1):
                if not isinstance(attempt, dict) or attempt.get("attempt") != index:
                    raise ReleaseCheckError("API attempts must be numbered consecutively")
                previous_response = attempt.get("response")
                if isinstance(previous_response, dict):
                    response_model = previous_response.get("model")
                    if (
                        not isinstance(response_model, str)
                        or not response_model
                        or response_model.startswith("demo")
                    ):
                        raise ReleaseCheckError("API response has an invalid model identity")
                    response_models.add(response_model)
            final = attempts[-1]
            response = final.get("response") if isinstance(final, dict) else None
            if (
                not isinstance(final, dict)
                or final.get("valid") is not True
                or not isinstance(response, dict)
            ):
                raise ReleaseCheckError("API stage did not finish with a validated response")
            response_id = response.get("id", "")
            content = response.get("content")
            usage = response.get("usage")
            if (
                not isinstance(response_id, str)
                or not response_id.startswith("msg_")
                or response.get("type") != "message"
                or response.get("stop_reason") != "end_turn"
                or not isinstance(content, list)
                or not content
                or not any(
                    isinstance(block, dict)
                    and block.get("type") == "text"
                    and isinstance(block.get("text"), str)
                    for block in content
                )
                or not isinstance(usage, dict)
                or not isinstance(usage.get("input_tokens"), int)
                or not isinstance(usage.get("output_tokens"), int)
                or usage["input_tokens"] <= 0
                or usage["output_tokens"] <= 0
            ):
                raise ReleaseCheckError("API response lacks real message metadata/token usage")
        _check_review_flags(result, stages, PROMPT_SETS[prompt_set]["review_flags"])
        if sorted(response_models) != meta.get("response_models") or len(response_models) != 1:
            raise ReleaseCheckError("all API attempts must record the same resolved model")
        common_response_models.add(tuple(sorted(response_models)))
    required = {(f"ex-001-a{number:02d}", mode) for number in (1, 2) for mode in ("rubric", "free")}
    if not required.issubset(seen):
        raise ReleaseCheckError("live gate needs public correct/incorrect answers in both modes")
    if len(common_configs) != 1:
        raise ReleaseCheckError("live examples must use the same model configuration")
    if len(common_response_models) != 1:
        raise ReleaseCheckError("both modes must use the same resolved API model")
    if require_tracked:
        if not _is_repository_root(root):
            raise ReleaseCheckError("tracked-evidence gate requires a Git repository")
        if any(not _is_tracked(root, path) for path in tracked_files):
            raise ReleaseCheckError(
                "reviewed public evidence and its inputs must be tracked in Git"
            )
    return len(runs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    parser.add_argument("--dist", type=Path, help="directory with one wheel and one sdist")
    parser.add_argument("--require-live", action="store_true", help="require genuine API examples")
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--require-tracked", action="store_true", help="require evidence in Git")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        count = check_sources(root)
        distributions = check_distributions(args.dist) if args.dist is not None else []
        live_runs = None
        if args.require_tracked and not args.require_live:
            raise ReleaseCheckError("--require-tracked requires --require-live")
        if args.require_live:
            evidence = args.evidence if args.evidence.is_absolute() else root / args.evidence
            live_runs = check_live_evidence(root, evidence, require_tracked=args.require_tracked)
        print(
            json.dumps(
                {
                    "status": "passed",
                    "public_files_checked": count,
                    "distributions": distributions,
                    "live_runs_checked": live_runs,
                    "note": "No API calls made; automated scan is not a full privacy audit.",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        subprocess.SubprocessError,
        zipfile.BadZipFile,
        tarfile.TarError,
    ) as exc:
        # Schema validation errors may include answer text; keep the public diagnostic concise.
        message = str(exc) if isinstance(exc, ReleaseCheckError) else type(exc).__name__
        print(f"Release check failed: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
