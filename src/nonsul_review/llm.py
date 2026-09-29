"""Anthropic calls, bounded validation retries, and auditable offline fixtures.

Only the problem and answer body enter model requests. API authentication comes
from the environment (optionally populated by a local .env); it is never part of
the prompts or returned artifacts. Response traces contain SDK JSON bodies, not
HTTP headers or exception messages.

The documented SDK contract is messages.create(), Message.content text blocks,
max_retries=0, and a float timeout:
https://platform.claude.com/docs/en/api/sdks/python
Python SDK 1.x removed temperature from named arguments; extra_body preserves
the caller's requested HTTP parameter for compatible models. Newer models may
require temperature=1; no setting is changed silently:
https://platform.claude.com/docs/en/about-claude/model-deprecations
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from nonsul_review.schema import Answer, JudgementItem, Problem, Result, ReviewFlag

T = TypeVar("T")
PROMPT_VERSIONS = {"rubric": "rubric-v1", "free": "free-v1"}  # v1 names, kept for callers
DEFAULT_PROMPT_SET = "v1"
PROMPT_SETS: dict[str, dict[str, Any]] = {
    "v1": {
        "common": "common-v1.txt",
        "rubric": ("rubric-item-v1.txt", "rubric-feedback-v1.txt"),
        "free": ("free-feedback-v1.txt", "free-table-v1.txt"),
        "review_flags": False,
    },
    "v2": {
        "common": "common-v2.txt",
        "rubric": ("rubric-item-v2.txt", "rubric-feedback-v2.txt"),
        "free": ("free-feedback-v2.txt", "free-table-v2.txt"),
        "review_flags": True,
    },
}


def prompt_version(mode: str, prompt_set: str) -> str:
    return f"{mode}-{prompt_set}"


def prompt_file_names(mode: str, prompt_set: str) -> set[str]:
    """All prompt files whose hashes a result of this procedure records."""
    files = PROMPT_SETS[prompt_set]
    return {files["common"], *files[mode]}


_SECRET_PATTERN = re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}")
_PRIVATE_RESPONSE_KEYS = {
    "authorization",
    "api_key",
    "x-api-key",
    "request_headers",
    "headers",
}


@dataclass(frozen=True)
class RunConfig:
    """Explicit, shared settings for both review procedures.

    max_retries applies to a stage, not the whole pipeline. Thus 2 permits at
    most 3 calls for a stage, including schema failures and transient API errors.
    """

    model: str | None = None
    temperature: float = 1.0
    max_tokens: int = 4096
    timeout: float = 60.0
    max_retries: int = 2
    demo: bool = False
    prompt_set: str = DEFAULT_PROMPT_SET

    def __post_init__(self) -> None:
        if not isinstance(self.prompt_set, str) or self.prompt_set not in PROMPT_SETS:
            raise ValueError(f"prompt_set must be one of {', '.join(sorted(PROMPT_SETS))}")
        if isinstance(self.temperature, bool) or not isinstance(self.temperature, (int, float)):
            raise ValueError("temperature must be a number between 0 and 1")
        if not math.isfinite(self.temperature) or not 0 <= self.temperature <= 1:
            raise ValueError("temperature must be a finite number between 0 and 1")
        if isinstance(self.max_tokens, bool) or not isinstance(self.max_tokens, int):
            raise ValueError("max_tokens must be a positive integer")
        if self.max_tokens <= 0:
            raise ValueError("max_tokens must be a positive integer")
        if isinstance(self.timeout, bool) or not isinstance(self.timeout, (int, float)):
            raise ValueError("timeout must be a positive number")
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("timeout must be a positive finite number")
        if isinstance(self.max_retries, bool) or not isinstance(self.max_retries, int):
            raise ValueError("max_retries must be an integer between 0 and 2")
        if not 0 <= self.max_retries <= 2:
            raise ValueError("max_retries must be an integer between 0 and 2")
        if not isinstance(self.demo, bool):
            raise ValueError("demo must be a boolean")
        if self.model is not None and not isinstance(self.model, str):
            raise ValueError("model must be a string")


class PipelineError(RuntimeError):
    """A failed run with its complete, sanitized per-attempt trace attached."""

    def __init__(self, message: str, raw: dict[str, Any]):
        super().__init__(message)
        self.raw = raw


class StageValidationError(ValueError):
    """A safe validation message that contains no model-generated values."""


class _Feedback(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    overall_feedback: str = Field(min_length=1, max_length=200_000)


class _Table(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    items: list[JudgementItem]


class _FeedbackWithFlags(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    overall_feedback: str = Field(min_length=1, max_length=200_000)
    review_flags: list[ReviewFlag] = Field(max_length=500)


class _TableWithFlags(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    items: list[JudgementItem]
    review_flags: list[ReviewFlag] = Field(max_length=500)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def input_fingerprint(problem: Problem, answer_body: str) -> str:
    """Hash only the source information visible to the model."""
    return _digest({"problem": problem.model_dump(mode="json"), "answer_body": answer_body})


def config_fingerprint(config: RunConfig, provider: str) -> str:
    """Hash shared settings; procedure, prompts, IDs, and time are excluded."""
    return _digest(
        {
            "provider": provider,
            "model": config.model,
            "temperature": float(config.temperature),
            "max_tokens": config.max_tokens,
            "timeout": float(config.timeout),
            "max_retries": config.max_retries,
        }
    )


def _load_local_env() -> None:
    # Explicit cwd avoids searching unrelated parent directories for credentials.
    from dotenv import load_dotenv

    load_dotenv(dotenv_path=Path.cwd() / ".env", override=False)


def _resolve_config(config: RunConfig) -> RunConfig:
    if config.demo:
        return replace(config, model="demo-fixture")
    model = config.model or os.environ.get("NONSUL_MODEL")
    if not model or not model.strip():
        raise ValueError("A model is required: pass --model or set NONSUL_MODEL.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}", model):
        raise ValueError("model must be a model identifier without whitespace")
    if model == os.environ.get("ANTHROPIC_API_KEY"):
        raise ValueError("model must be a model identifier, not a credential")
    return replace(config, model=model)


def _sanitize(value: Any) -> Any:
    """Defense in depth for auth values accidentally echoed by an upstream SDK."""
    if isinstance(value, str):
        for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
            secret = os.environ.get(name)
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return _SECRET_PATTERN.sub("[REDACTED]", value)
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if str(key).lower() in _PRIVATE_RESPONSE_KEYS else _sanitize(item)
            for key, item in value.items()
        }
    if isinstance(value, (tuple, list)):
        return [_sanitize(item) for item in value]
    return value


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            raise StageValidationError("Duplicate JSON object keys are not allowed.")
        obj[key] = value
    return obj


def _reject_constant(_: str) -> Any:
    raise StageValidationError("Non-finite JSON numbers are not allowed.")


def _parse_json(text: str) -> Any:
    try:
        parsed = json.loads(
            text, object_pairs_hook=_no_duplicate_keys, parse_constant=_reject_constant
        )
    except (json.JSONDecodeError, RecursionError) as exc:
        raise StageValidationError("Response is not a single valid JSON object.") from exc
    if not isinstance(parsed, dict):
        raise StageValidationError("Response must be a JSON object.")
    return parsed


def _response_body(response: Any) -> dict[str, Any]:
    if isinstance(response, dict):
        body = response
    elif callable(getattr(response, "model_dump", None)):
        # The SDK model excludes HTTP response headers and the private request ID.
        body = response.model_dump(mode="json")
    else:
        raise StageValidationError("Provider did not return a Messages API object.")
    if not isinstance(body, dict):
        raise StageValidationError("Provider response body is not a JSON object.")
    return _sanitize(body)


def _response_text(body: dict[str, Any]) -> str:
    if body.get("stop_reason") == "max_tokens":
        raise StageValidationError("Model output reached max_tokens; response may be incomplete.")
    if body.get("stop_reason") in {"refusal", "tool_use", "pause_turn"}:
        raise StageValidationError("Model did not complete a judgement response.")
    content = body.get("content")
    if not isinstance(content, list):
        raise StageValidationError("Provider response has no text content blocks.")
    texts = [
        block["text"]
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    ]
    if not texts:
        raise StageValidationError("Provider response has no text content blocks.")
    return "\n".join(texts)


def _validation_summary(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, ValidationError):
        # Never serialize Pydantic's input values or exception context.
        fields = [
            {
                "path": ".".join(str(part) for part in item["loc"]),
                "type": item["type"],
            }
            for item in exc.errors(include_input=False, include_context=False, include_url=False)
        ]
        return _sanitize({"type": "schema_validation", "detail": fields[:10]})
    if isinstance(exc, StageValidationError):
        return {"type": "response_validation", "detail": str(exc)}
    return {"type": "response_processing", "detail": type(exc).__name__}


def _api_error(exc: Exception) -> dict[str, Any]:
    status = getattr(exc, "status_code", None)
    result: dict[str, Any] = {"type": type(exc).__name__}
    if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599:
        result["status"] = status
    return result


def _is_transient(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    return (
        status in {408, 409, 429}
        or (isinstance(status, int) and 500 <= status <= 599)
        or type(exc).__name__ in {"APIConnectionError", "APITimeoutError"}
    )


def _make_client(config: RunConfig) -> Any:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key or not key.strip():
        raise ValueError("Set ANTHROPIC_API_KEY in the environment or local .env file.")
    from anthropic import Anthropic

    # Explicit endpoint keeps an unrelated ANTHROPIC_BASE_URL from redirecting
    # credentials. No SDK retries: the pipeline records every actual attempt.
    return Anthropic(
        api_key=key,
        base_url="https://api.anthropic.com",
        max_retries=0,
        timeout=float(config.timeout),
    )


class DemoClient:
    """Return labeled, authored fixture responses for exact public inputs only.

    This does not evaluate new answers and does not consult answer identifiers,
    error labels, split, source, or alternative-solution metadata.
    """

    def __init__(self, problem: Problem, answer_body: str, with_review_flags: bool = False):
        self._with_review_flags = with_review_flags
        path = resources.files("nonsul_review").joinpath("demo", "cases.json")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            fixture_problem = Problem.model_validate(data["problem"])
            cases = data["cases"]
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise ValueError("Bundled demo fixtures are missing or invalid.") from exc
        if fixture_problem.model_dump(mode="json") != problem.model_dump(mode="json"):
            raise ValueError("Demo mode accepts only the exact bundled example problem.")
        matching = [case for case in cases if case.get("body") == answer_body]
        if len(matching) != 1:
            raise ValueError("Demo mode accepts only exact bundled example answer bodies.")
        self._case = matching[0]
        self._source = {
            "problem": problem.model_dump(mode="json"),
            "answer_body": answer_body,
        }
        self.messages = self

    def create(self, **kwargs: Any) -> dict[str, Any]:
        payload = json.loads(kwargs["messages"][0]["content"])
        if payload["source"] != self._source:
            raise ValueError("Demo source differs from its exact bundled fixture.")
        workflow = payload["workflow"]
        if "target_rubric_id" in workflow:
            items = [
                item
                for item in self._case["items"]
                if item["rubric_id"] == workflow["target_rubric_id"]
            ]
            if len(items) != 1:
                raise ValueError("Bundled demo rubric fixture is invalid.")
            result = items[0]
        elif workflow["stage"] == "free:table":
            result = {"items": self._case["items"]}
            if self._with_review_flags:
                result["review_flags"] = []
        else:
            result = {"overall_feedback": self._case["overall_feedback"]}
            if self._with_review_flags and workflow["stage"] == "rubric:feedback":
                result["review_flags"] = []
        return {
            "id": "demo-authored-fixture",
            "type": "message",
            "role": "assistant",
            "model": "demo-fixture",
            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }


class ReviewSession:
    """Shared request source, validators, and trace across one procedure."""

    def __init__(self, problem: Problem, answer: Answer, mode: str, config: RunConfig):
        self.problem = problem
        self.answer = answer
        self.mode = mode
        self.config = config
        self.client: Any = None
        self.source = {
            "problem": problem.model_dump(mode="json"),
            "answer_body": answer.body,
        }
        self.provider = "demo" if config.demo else "anthropic"
        self.prompt_set = config.prompt_set
        self.files = PROMPT_SETS[config.prompt_set]
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.prompt_hashes: dict[str, str] = {}
        self.raw: dict[str, Any] = {
            "schema_version": "1.0",
            "status": "running",
            "provider": self.provider,
            "model": config.model,
            "is_demo": config.demo,
            "mode": mode,
            "prompt_version": prompt_version(mode, config.prompt_set),
            "input_sha256": input_fingerprint(problem, answer.body),
            "config_sha256": config_fingerprint(config, self.provider),
            "created_at": self.created_at,
            "settings": {
                "temperature": float(config.temperature),
                "max_tokens": config.max_tokens,
                "timeout": float(config.timeout),
                "max_retries": config.max_retries,
            },
            "stages": [],
        }

    def _fail(self, message: str) -> PipelineError:
        self.raw["status"] = "failed"
        message = _sanitize(message)
        self.raw["failure"] = message
        self.raw = _sanitize(self.raw)
        return PipelineError(message, self.raw)

    def _prompt(self, filename: str) -> str:
        text = (
            resources.files("nonsul_review")
            .joinpath("prompts", filename)
            .read_text(encoding="utf-8")
        )
        self.prompt_hashes[filename] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return text

    def validate_items(
        self, data: Any, expected_ids: list[str] | None = None
    ) -> list[JudgementItem]:
        if not isinstance(data, list):
            raise StageValidationError("items must be an array.")
        items = [JudgementItem.model_validate(item) for item in data]
        expected = (
            expected_ids
            if expected_ids is not None
            else [rubric.id for rubric in self.problem.rubric]
        )
        found = [item.rubric_id for item in items]
        if len(found) != len(set(found)):
            raise StageValidationError("Duplicate rubric IDs are not allowed.")
        if len(found) != len(expected) or set(found) != set(expected):
            raise StageValidationError("Rubric IDs must match the requested items exactly.")
        for item in items:
            if item.verdict is None and not item.needs_review:
                raise StageValidationError("A null verdict requires needs_review=true.")
            if item.evidence and not item.evidence.strip():
                raise StageValidationError("Evidence must be a meaningful quote or empty string.")
            if item.verdict in {"met", "partial"} and not item.evidence.strip():
                raise StageValidationError("met and partial require a nonempty answer quotation.")
            if item.evidence and item.evidence not in self.answer.body:
                raise StageValidationError(
                    "Evidence must occur verbatim in the original answer body."
                )
        indexed = {item.rubric_id: item for item in items}
        return [indexed[rubric_id] for rubric_id in expected]

    def validate_feedback(self, data: Any) -> str:
        feedback = _Feedback.model_validate(data).overall_feedback
        if not feedback.strip():
            raise StageValidationError("overall_feedback must not be blank.")
        return feedback

    def validate_table(self, data: Any) -> list[JudgementItem]:
        table = _Table.model_validate(data)
        return self.validate_items([item.model_dump(mode="json") for item in table.items])

    def validate_flags(self, flags: list[ReviewFlag]) -> list[ReviewFlag]:
        known = {rubric.id for rubric in self.problem.rubric}
        for flag in flags:
            if flag.rubric_id not in known:
                raise StageValidationError("review_flags must reference the problem's rubric IDs.")
            if not flag.note.strip():
                raise StageValidationError("review_flags notes must not be blank.")
        return flags

    def validate_feedback_with_flags(self, data: Any) -> tuple[str, list[ReviewFlag]]:
        parsed = _FeedbackWithFlags.model_validate(data)
        if not parsed.overall_feedback.strip():
            raise StageValidationError("overall_feedback must not be blank.")
        return parsed.overall_feedback, self.validate_flags(parsed.review_flags)

    def validate_table_with_flags(self, data: Any) -> tuple[list[JudgementItem], list[ReviewFlag]]:
        parsed = _TableWithFlags.model_validate(data)
        items = self.validate_items([item.model_dump(mode="json") for item in parsed.items])
        return items, self.validate_flags(parsed.review_flags)

    def call_json(
        self,
        *,
        stage: str,
        prompt_file: str,
        workflow: dict[str, Any],
        validator: Callable[[Any], T],
    ) -> T:
        system = self._prompt(self.files["common"]) + "\n\n" + self._prompt(prompt_file)
        stage_trace: dict[str, Any] = {
            "stage": stage,
            "prompt_file": prompt_file,
            "prompt_sha256": hashlib.sha256(system.encode("utf-8")).hexdigest(),
            "attempts": [],
        }
        self.raw["stages"].append(stage_trace)
        previous_failure: dict[str, Any] | None = None
        for attempt in range(1, self.config.max_retries + 2):
            payload = {
                "source": self.source,
                "workflow": {"stage": stage, **workflow},
            }
            if previous_failure is not None:
                payload["format_retry"] = {
                    "attempt": attempt,
                    "instruction": "Regenerate a fresh response satisfying the stage requirements.",
                    "previous_failure": previous_failure,
                }
            record: dict[str, Any] = {"attempt": attempt, "response": None, "valid": False}
            stage_trace["attempts"].append(record)
            try:
                response = self.client.messages.create(
                    model=self.config.model,
                    extra_body={"temperature": float(self.config.temperature)},
                    max_tokens=self.config.max_tokens,
                    timeout=float(self.config.timeout),
                    system=system,
                    messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                )
            except Exception as exc:
                error = _api_error(exc)
                record["api_error"] = error
                if _is_transient(exc) and attempt <= self.config.max_retries:
                    previous_failure = {"type": "transient_api_error"}
                    time.sleep(min(2 ** (attempt - 1), 2))
                    continue
                raise self._fail(
                    f"Stage {stage} failed with {error['type']}"
                    + (f" (HTTP {error['status']})" if "status" in error else "")
                    + (
                        ". Check the selected model and max_tokens; newer Claude models require "
                        "temperature=1. No parameter was changed automatically."
                        if error.get("status") == 400
                        else ". Provider exception details were omitted to protect credentials."
                    )
                ) from None
            try:
                record["response"] = _response_body(response)
                text = _response_text(record["response"])
                result = validator(_parse_json(text))
            except Exception as exc:
                error = _validation_summary(exc)
                record["validation_error"] = error
                previous_failure = error
                if attempt <= self.config.max_retries:
                    continue
                raise self._fail(
                    f"Stage {stage} failed validation after {attempt} attempts."
                ) from None
            record["valid"] = True
            return result
        raise AssertionError("bounded stage loop must return or raise")

    def result_meta(self) -> dict[str, Any]:
        attempts = [attempt for stage in self.raw["stages"] for attempt in stage["attempts"]]
        response_models = sorted(
            {
                attempt["response"]["model"]
                for attempt in attempts
                if isinstance(attempt.get("response"), dict)
                and isinstance(attempt["response"].get("model"), str)
            }
        )
        return {
            "provider": self.provider,
            "is_demo": self.config.demo,
            "model": self.config.model,
            "response_models": response_models,
            "prompt_version": prompt_version(self.mode, self.prompt_set),
            "prompt_sha256": self.prompt_hashes,
            "temperature": float(self.config.temperature),
            "max_tokens": self.config.max_tokens,
            "timeout": float(self.config.timeout),
            "max_retries": self.config.max_retries,
            "config_sha256": self.raw["config_sha256"],
            "input_sha256": self.raw["input_sha256"],
            "answer_body_sha256": hashlib.sha256(self.answer.body.encode("utf-8")).hexdigest(),
            "created_at": self.created_at,
            "calls": len(attempts),
            "retry_count": len(attempts) - len(self.raw["stages"]),
        }


def apply_review_flags(items: list[JudgementItem], flags: list[ReviewFlag]) -> list[JudgementItem]:
    """needs_review = original OR flagged; verdicts and reasons stay unchanged."""
    flagged = {flag.rubric_id for flag in flags}
    return [
        item.model_copy(update={"needs_review": True})
        if item.rubric_id in flagged and not item.needs_review
        else item
        for item in items
    ]


def run_review(
    problem: Problem,
    answer: Answer,
    mode: str,
    config: RunConfig,
    client: Any = None,
) -> tuple[Result, dict[str, Any]]:
    """Execute a complete review and return the validated draft plus raw trace.

    An injected client must expose messages.create(**kwargs), returning either
    an Anthropic Message or its JSON body. SDK clients supporting with_options
    have hidden retries disabled; simple test doubles must not retry internally.
    """
    if mode not in PROMPT_VERSIONS:
        raise ValueError("mode must be rubric or free")
    if answer.problem != problem.id:
        raise ValueError("Answer problem ID does not match the supplied problem.")
    _load_local_env()
    resolved = _resolve_config(config)
    session = ReviewSession(problem, answer, mode, resolved)
    owned_client = client is None
    try:
        if resolved.demo:
            if client is not None:
                raise session._fail("Demo mode does not accept a custom provider client.")
            try:
                session.client = DemoClient(
                    problem, answer.body, with_review_flags=session.files["review_flags"]
                )
            except ValueError as exc:
                raise session._fail(str(exc)) from None
        elif client is not None:
            with_options = getattr(client, "with_options", None)
            session.client = (
                with_options(max_retries=0, timeout=float(resolved.timeout))
                if callable(with_options)
                else client
            )
        else:
            try:
                session.client = _make_client(resolved)
            except ValueError:
                raise session._fail(
                    "Set ANTHROPIC_API_KEY in the environment or local .env file."
                ) from None
            except Exception as exc:
                raise session._fail(
                    f"Could not initialize the provider client ({type(exc).__name__})."
                ) from None

        if mode == "rubric":
            from nonsul_review.modes.rubric import run
        else:
            from nonsul_review.modes.free import run
        items, feedback, flags = run(session)
        result = Result(
            answer_id=answer.id,
            problem_id=problem.id,
            mode=mode,
            items=apply_review_flags(items, flags),
            overall_feedback=feedback,
            review_flags=flags,
            meta=session.result_meta(),
        )
        session.raw["status"] = "succeeded"
        session.raw["prompt_sha256"] = session.prompt_hashes
        return result, session.raw
    except PipelineError:
        raise
    except Exception as exc:
        raise session._fail(f"Review pipeline failed ({type(exc).__name__}).") from None
    finally:
        if owned_client and session.client is not None:
            close = getattr(session.client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
