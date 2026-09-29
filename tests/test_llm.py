"""Contract and failure-path tests; all providers are local mocks."""

from __future__ import annotations

import importlib
import inspect
import json
from types import SimpleNamespace

import pytest

from nonsul_review.llm import PipelineError, RunConfig, _make_client, run_review
from nonsul_review.schema import Answer, Problem


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
            "alternatives": "같은 결론의 대안 논증을 허용한다.",
            "rubric": [
                {"id": "R1", "step": "관계식", "criteria": "x=1 유도"},
                {"id": "R2", "step": "결론", "criteria": "y=2 유도"},
            ],
        }
    )
    answer = Answer(
        id="hidden-answer-id",
        problem=problem.id,
        source="synthetic",
        error_types=["E-private-label"],
        is_alternative=True,
        split="test",
        body="조건에 의해 x=1이다. 따라서 y=2이다.",
    )
    return problem, answer


def item(rubric_id="R1", evidence="x=1", verdict="met", **extra):
    return {
        "rubric_id": rubric_id,
        "verdict": verdict,
        "evidence": evidence,
        "reason": "답안에서 근거를 확인했다.",
        "feedback": "해당 논증을 유지하세요.",
        "needs_review": False,
        **extra,
    }


def response(value, **extra):
    return {
        "id": "msg_mock",
        "type": "message",
        "role": "assistant",
        "model": "test-model",
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "content": [
            {"type": "text", "text": value if isinstance(value, str) else json.dumps(value)}
        ],
        "usage": {"input_tokens": 10, "output_tokens": 10},
        **extra,
    }


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return value


def test_retries_invalid_quote_then_retains_every_attempt(inputs):
    problem, answer = inputs
    bad = response(item(evidence="invented quotation"))
    client = FakeClient(
        [
            bad,
            response(item()),
            response(item("R2", "y=2")),
            response({"overall_feedback": "두 단계를 모두 확인했다."}),
        ]
    )
    result, raw = run_review(problem, answer, "rubric", RunConfig(model="test-model"), client)
    assert [entry.verdict for entry in result.items] == ["met", "met"]
    assert result.meta["calls"] == 4
    assert result.meta["retry_count"] == 1
    assert [len(stage["attempts"]) for stage in raw["stages"]] == [2, 1, 1]
    first, second = raw["stages"][0]["attempts"]
    assert first["response"] == bad
    assert first["valid"] is False and second["valid"] is True
    assert "format_retry" in json.loads(client.calls[1]["messages"][0]["content"])


@pytest.mark.parametrize(
    "bad_item",
    [
        item("R9"),
        item(evidence=""),
        item(evidence="   "),
        item(verdict="partial", evidence="not in this answer"),
        item(verdict=None, evidence="", needs_review=False),
        item(unexpected="extra"),
    ],
)
def test_invalid_item_stops_after_three_calls_and_exposes_raw(inputs, bad_item):
    problem, answer = inputs
    client = FakeClient([response(bad_item)] * 4)
    with pytest.raises(PipelineError) as captured:
        run_review(problem, answer, "rubric", RunConfig(model="test-model"), client)
    assert len(client.calls) == 3
    raw = captured.value.raw
    assert raw["status"] == "failed"
    assert len(raw["stages"]) == 1
    assert len(raw["stages"][0]["attempts"]) == 3
    assert all(not attempt["valid"] for attempt in raw["stages"][0]["attempts"])


@pytest.mark.parametrize(
    "text",
    [
        "```json\n{}\n```",
        "not json",
        "[]",
        '{"a":1,"a":2}',
        '{"a":NaN}',
        '{"a":' + "[" * 1100 + "0" + "]" * 1100 + "}",
    ],
)
def test_malformed_json_uses_same_bounded_failure_path(inputs, text):
    problem, answer = inputs
    client = FakeClient([response(text)] * 3)
    with pytest.raises(PipelineError) as captured:
        run_review(problem, answer, "rubric", RunConfig(model="test-model"), client)
    attempts = captured.value.raw["stages"][0]["attempts"]
    assert len(attempts) == 3
    assert all(attempt["response"]["content"][0]["text"] == text for attempt in attempts)


def test_valid_null_is_not_converted_to_an_error_verdict(inputs):
    problem, answer = inputs
    client = FakeClient(
        [
            response(item(verdict=None, evidence="", needs_review=True)),
            response(item("R2", "y=2")),
            response({"overall_feedback": "R1은 강사 확인 필요."}),
        ]
    )
    result, _ = run_review(problem, answer, "rubric", RunConfig(model="test-model"), client)
    assert result.items[0].verdict is None
    assert result.items[0].needs_review is True


def test_truncated_output_is_not_accepted_even_when_json_parses(inputs):
    problem, answer = inputs
    client = FakeClient([response(item(), stop_reason="max_tokens")] * 3)
    with pytest.raises(PipelineError) as captured:
        run_review(problem, answer, "rubric", RunConfig(model="test-model"), client)
    assert len(captured.value.raw["stages"][0]["attempts"]) == 3


def test_oversize_feedback_fails_at_its_stage_with_all_raw(inputs):
    problem, answer = inputs
    client = FakeClient([response({"overall_feedback": "가" * 200_001})] * 3)
    with pytest.raises(PipelineError) as captured:
        run_review(problem, answer, "free", RunConfig(model="test-model"), client)
    assert captured.value.raw["stages"][0]["stage"] == "free:feedback"
    assert len(captured.value.raw["stages"][0]["attempts"]) == 3


def test_api_exception_never_serializes_credential_or_exception_text(inputs, monkeypatch):
    problem, answer = inputs
    secret = "test-secret-" + "z" * 24
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)

    class AuthenticationError(Exception):
        status_code = 401

    client = FakeClient([AuthenticationError(f"Authorization: {secret}")])
    with pytest.raises(PipelineError) as captured:
        run_review(problem, answer, "rubric", RunConfig(model="test-model"), client)
    assert len(client.calls) == 1
    assert secret not in str(captured.value)
    assert secret not in json.dumps(captured.value.raw)
    assert captured.value.raw["stages"][0]["attempts"][0]["api_error"] == {
        "type": "AuthenticationError",
        "status": 401,
    }


def test_transient_api_and_schema_errors_share_one_three_attempt_budget(inputs, monkeypatch):
    problem, answer = inputs
    monkeypatch.setattr("nonsul_review.llm.time.sleep", lambda _: None)

    class RateLimitError(Exception):
        status_code = 429

    client = FakeClient([RateLimitError("first"), response("malformed"), RateLimitError("last")])
    with pytest.raises(PipelineError) as captured:
        run_review(problem, answer, "rubric", RunConfig(model="test-model"), client)
    assert len(client.calls) == 3
    attempts = captured.value.raw["stages"][0]["attempts"]
    assert "api_error" in attempts[0]
    assert "validation_error" in attempts[1]
    assert "api_error" in attempts[2]


def test_model_resolution_is_explicit_and_environment_supported(inputs, monkeypatch):
    problem, answer = inputs
    with pytest.raises(ValueError, match="model is required"):
        run_review(problem, answer, "rubric", RunConfig(), FakeClient([]))
    monkeypatch.setenv("NONSUL_MODEL", "test-model")
    client = FakeClient(
        [
            response(item()),
            response(item("R2", "y=2")),
            response({"overall_feedback": "확인 완료."}),
        ]
    )
    result, _ = run_review(problem, answer, "rubric", RunConfig(), client)
    assert result.meta["model"] == "test-model"


def test_local_dotenv_is_read_without_overriding_environment(inputs, monkeypatch, tmp_path):
    problem, answer = inputs
    (tmp_path / ".env").write_text("NONSUL_MODEL=from-file\n", encoding="utf-8")
    monkeypatch.setenv("NONSUL_MODEL", "from-environment")
    client = FakeClient(
        [
            response(item()),
            response(item("R2", "y=2")),
            response({"overall_feedback": "확인 완료."}),
        ]
    )
    result, _ = run_review(problem, answer, "rubric", RunConfig(), client)
    assert result.meta["model"] == "from-environment"


def test_current_sdk_constructor_disables_retries_and_honors_timeout(inputs, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only-not-a-real-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://not-anthropic.invalid")
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        monkeypatch.delenv(name, raising=False)
    with _make_client(RunConfig(model="test-model", timeout=17)) as client:
        assert client.max_retries == 0
        assert client.timeout == 17
        assert str(client.base_url).rstrip("/") == "https://api.anthropic.com"


def test_current_sdk_encodes_temperature_and_parses_messages_without_network(inputs):
    """A real SDK with a mock HTTP transport catches signature/API-shape drift."""
    from anthropic import Anthropic

    problem, answer = inputs
    annotation = str(inspect.signature(Anthropic).parameters["http_client"].annotation)
    http = importlib.import_module("httpx2" if "httpx2" in annotation else "httpx")
    pending = iter(
        [
            response(item()),
            response(item("R2", "y=2")),
            response({"overall_feedback": "확인 완료."}),
        ]
    )
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return http.Response(200, json=next(pending))

    with http.Client(transport=http.MockTransport(handler)) as transport:
        with Anthropic(
            api_key="test-only-not-a-real-key",
            max_retries=0,
            http_client=transport,
            timeout=5,
        ) as client:
            result, raw = run_review(
                problem, answer, "rubric", RunConfig(model="test-model", temperature=1), client
            )
    assert len(requests) == 3
    assert all(request["temperature"] == 1.0 for request in requests)
    assert all("metadata" not in request for request in requests)
    assert result.meta["temperature"] == 1.0
    assert raw["status"] == "succeeded"
    assert "test-only-not-a-real-key" not in json.dumps(raw)


def test_injected_sdk_hidden_retries_are_disabled_and_every_http_attempt_is_saved(
    inputs, monkeypatch
):
    from anthropic import Anthropic

    problem, answer = inputs
    monkeypatch.setattr("nonsul_review.llm.time.sleep", lambda _: None)
    annotation = str(inspect.signature(Anthropic).parameters["http_client"].annotation)
    http = importlib.import_module("httpx2" if "httpx2" in annotation else "httpx")
    calls = []

    def handler(request):
        calls.append(request)
        return http.Response(
            500,
            json={
                "type": "error",
                "error": {
                    "type": "api_error",
                    "message": "mock server error; never sent over network",
                },
            },
        )

    with http.Client(transport=http.MockTransport(handler)) as transport:
        with Anthropic(
            api_key="test-only-not-a-real-key",
            max_retries=2,
            http_client=transport,
        ) as client:
            with pytest.raises(PipelineError) as captured:
                run_review(problem, answer, "rubric", RunConfig(model="test-model"), client)
    assert len(calls) == 3
    attempts = captured.value.raw["stages"][0]["attempts"]
    assert len(attempts) == 3
    assert all(attempt["api_error"]["status"] == 500 for attempt in attempts)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_retries": 3},
        {"max_retries": -1},
        {"max_retries": True},
        {"max_tokens": 0},
        {"max_tokens": 1.5},
        {"timeout": float("inf")},
        {"timeout": 0},
        {"temperature": float("nan")},
        {"temperature": 1.1},
        {"temperature": True},
    ],
)
def test_invalid_configuration_is_rejected_before_any_api_call(kwargs):
    with pytest.raises(ValueError):
        RunConfig(**kwargs)
