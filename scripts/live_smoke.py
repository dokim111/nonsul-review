#!/usr/bin/env python3
"""Run the public synthetic examples against a real API, then record hashed evidence.

This is an explicit, billed command. It is never run by ordinary CI. API credentials
are read from the environment or local .env and are not written into the manifest.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from release_check import ROOT, check_bytes, check_live_evidence, sha256_file, source_fingerprint


def _descriptor(path: Path) -> dict[str, str]:
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": sha256_file(path)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", help="exact Anthropic model ID; otherwise NONSUL_MODEL")
    parser.add_argument("--out", type=Path, default=ROOT / "examples/ex-001/live")
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument(
        "--prompt-set",
        choices=("v1", "v2"),
        default="v2",
        help="Procedure version; the release gate requires the current version (v2)",
    )
    args = parser.parse_args(argv)
    load_dotenv(ROOT / ".env", override=False)
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        print("Set ANTHROPIC_API_KEY in your environment or local .env first.", file=sys.stderr)
        return 2
    model = args.model or os.environ.get("NONSUL_MODEL", "").strip()
    if not model:
        print("Pass --model or set NONSUL_MODEL to an exact available model ID.", file=sys.stderr)
        return 2
    out = args.out.resolve()
    if not out.is_relative_to(ROOT) or out == ROOT:
        print("Use a new output directory inside this source checkout.", file=sys.stderr)
        return 2
    if out.exists() and any(out.iterdir()):
        print(
            "Output directory is not empty; use a new directory to preserve prior evidence.",
            file=sys.stderr,
        )
        return 2
    out.mkdir(parents=True, exist_ok=True)
    problem = ROOT / "examples/ex-001/problem.yaml"
    runs: list[dict[str, Any]] = []
    print("Running 4 real-API examples (2 answers × 2 modes); API charges may apply.")
    for answer_no in (1, 2):
        answer = ROOT / f"examples/ex-001/answers/a{answer_no:02d}.md"
        answer_id = f"ex-001-a{answer_no:02d}"
        for mode in ("rubric", "free"):
            command = [
                sys.executable,
                "-m",
                "nonsul_review",
                "run",
                "--problem",
                str(problem),
                "--answer",
                str(answer),
                "--mode",
                mode,
                "--out",
                str(out),
                "--model",
                model,
                "--temperature",
                str(args.temperature),
                "--max-tokens",
                str(args.max_tokens),
                "--timeout",
                str(args.timeout),
                "--prompt-set",
                args.prompt_set,
            ]
            print(f"Live example: {answer_id} / {mode}", flush=True)
            completed = subprocess.run(command, cwd=ROOT, capture_output=True, check=False)
            if completed.returncode:
                print(
                    f"Live example failed (exit {completed.returncode}). Inspect the local "
                    "failure/raw files; no success manifest has been written.",
                    file=sys.stderr,
                )
                return 1
            result_path = out / f"{answer_id}.{mode}.json"
            raw_path = out / f"{answer_id}.{mode}.raw.json"
            for path in (result_path, raw_path):
                check_bytes(path.relative_to(ROOT).as_posix(), path.read_bytes())
            runs.append(
                {
                    "answer_id": answer_id,
                    "problem_id": "ex-001",
                    "mode": mode,
                    "problem": _descriptor(problem),
                    "answer": _descriptor(answer),
                    "result": _descriptor(result_path),
                    "raw": _descriptor(raw_path),
                }
            )
    from nonsul_review import __version__

    try:
        git = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False
        )
        commit = git.stdout.strip() if git.returncode == 0 else None
    except FileNotFoundError:
        commit = None
    manifest = {
        "schema_version": "1.0",
        "tool_version": __version__,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "provider": "anthropic",
        "is_demo": False,
        "model": model,
        "prompt_set": args.prompt_set,
        "git_commit": commit,
        "source_sha256": source_fingerprint(ROOT),
        "runs": runs,
    }
    evidence = out / "evidence.json"
    # Write only after every API execution succeeded, then apply the same release validator.
    with evidence.open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    try:
        check_live_evidence(ROOT, evidence)
    except Exception:
        evidence.unlink(missing_ok=True)
        print(
            "Live evidence validation failed; raw/results remain for inspection. "
            "No success manifest remains.",
            file=sys.stderr,
        )
        return 1
    print(f"Validated live evidence: {evidence.relative_to(ROOT).as_posix()}")
    print("Review the public synthetic outputs before explicitly adding evidence files to Git.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
