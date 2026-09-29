"""Tests the actual procedure order and equality of available source data."""

from __future__ import annotations

import hashlib
import json
from importlib import resources
from types import SimpleNamespace

import pytest

from nonsul_review.llm import PipelineError, RunConfig, input_fingerprint, run_review
from nonsul_review.schema import Answer, Problem


def judgement(rubric_id, evidence):
    return {
        "rubric_id": rubric_id,
        "verdict": "met",
        "evidence": evidence,
        "reason": "충분한 논증이다.",
        "feedback": "이 논증을 유지하세요.",
        "needs_review": False,
    }


class ProcedureClient:
    def __init__(self, table=None):
        self.calls = []
        self.messages = SimpleNamespace(create=self.create)
        self.table = table or [judgement("R1", "x=1"), judgement("R2", "y=2")]

    def create(self, **kwargs):
        self.calls.append(kwargs)
        payload = json.loads(kwargs["messages"][0]["content"])
        workflow = payload["workflow"]
        if "target_rubric_id" in workflow:
            value = next(
                item for item in self.table if item["rubric_id"] == workflow["target_rubric_id"]
            )
        elif workflow["stage"] == "free:table":
            value = {"items": self.table}
        else:
            value = {"overall_feedback": "답안 전체에 대한 피드백이다."}
        return {
            "id": "msg_mock",
            "type": "message",
            "role": "assistant",
            "model": "test-model",
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": json.dumps(value)}],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }


@pytest.fixture
def source(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    problem = Problem.model_validate(
        {
            "id": "p1",
            "title": "테스트",
            "prompt": "x와 y를 구하시오.",
            "model_answer": "x=1, y=2",
            "alternatives": "대안 풀이를 허용한다.",
            "rubric": [
                {"id": "R1", "step": "x", "criteria": "x=1"},
                {"id": "R2", "step": "y", "criteria": "y=2"},
            ],
        }
    )
    answer = Answer(
        id="unique-hidden-answer",
        problem="p1",
        source="synthetic",
        error_types=["E-hidden"],
        split="test",
        is_alternative=True,
        body="x=1이므로 y=2이다.",
    )
    return problem, answer


def payloads(client):
    return [json.loads(call["messages"][0]["content"]) for call in client.calls]


def test_procedures_have_distinct_order_and_identical_original_information(source):
    problem, answer = source
    rubric_client, free_client = ProcedureClient(), ProcedureClient()
    config = RunConfig(model="test-model", temperature=0)
    rubric, rubric_raw = run_review(problem, answer, "rubric", config, rubric_client)
    free, free_raw = run_review(problem, answer, "free", config, free_client)
    rubric_payloads, free_payloads = payloads(rubric_client), payloads(free_client)
    assert [payload["workflow"]["stage"] for payload in rubric_payloads] == [
        "rubric:R1",
        "rubric:R2",
        "rubric:feedback",
    ]
    assert [payload["workflow"]["stage"] for payload in free_payloads] == [
        "free:feedback",
        "free:table",
    ]
    original = {"problem": problem.model_dump(mode="json"), "answer_body": answer.body}
    assert all(payload["source"] == original for payload in rubric_payloads + free_payloads)
    assert all("judgements" not in payload["workflow"] for payload in rubric_payloads[:2])
    assert len(rubric_payloads[-1]["workflow"]["judgements"]) == 2
    assert free_payloads[0]["workflow"] == {"stage": "free:feedback"}
    assert free_payloads[1]["workflow"]["overall_feedback"] == free.overall_feedback
    for client in (rubric_client, free_client):
        assert all(call["extra_body"]["temperature"] == 0.0 for call in client.calls)
    assert rubric.meta["config_sha256"] == free.meta["config_sha256"]
    assert rubric.meta["input_sha256"] == free.meta["input_sha256"]
    assert rubric.meta["answer_body_sha256"] == hashlib.sha256(answer.body.encode()).hexdigest()
    assert rubric.meta["input_sha256"] == input_fingerprint(problem, answer.body)
    assert rubric_raw["provider"] == free_raw["provider"] == "anthropic"


def test_frontmatter_change_does_not_change_model_requests_or_input_hash(source):
    problem, answer = source
    changed = answer.model_copy(
        update={
            "id": "another-answer",
            "source": "learner",
            "error_types": ["E-other"],
            "split": "dev",
            "is_alternative": False,
        }
    )
    before, after = ProcedureClient(), ProcedureClient()
    first, _ = run_review(problem, answer, "free", RunConfig(model="test-model"), before)
    second, _ = run_review(problem, changed, "free", RunConfig(model="test-model"), after)
    assert before.calls == after.calls
    assert first.meta["input_sha256"] == second.meta["input_sha256"]
    serialized = json.dumps(before.calls)
    for forbidden in (answer.id, "E-hidden", "error_types", "is_alternative", '"split"'):
        assert forbidden not in serialized
    assert first.answer_id != second.answer_id


@pytest.mark.parametrize(
    "table",
    [
        [judgement("R1", "x=1")],
        [judgement("R1", "x=1"), judgement("R1", "x=1")],
        [judgement("R1", "x=1"), judgement("R3", "y=2")],
    ],
)
def test_free_table_rejects_missing_duplicate_and_unknown_ids(source, table):
    problem, answer = source
    client = ProcedureClient(table)
    with pytest.raises(PipelineError) as captured:
        run_review(problem, answer, "free", RunConfig(model="test-model"), client)
    assert len(client.calls) == 4
    stages = captured.value.raw["stages"]
    assert stages[0]["attempts"][0]["valid"] is True
    assert len(stages[1]["attempts"]) == 3


def test_free_table_normalizes_valid_reordered_items(source):
    problem, answer = source
    client = ProcedureClient([judgement("R2", "y=2"), judgement("R1", "x=1")])
    result, _ = run_review(problem, answer, "free", RunConfig(model="test-model"), client)
    assert [item.rubric_id for item in result.items] == ["R1", "R2"]


def test_demo_is_exact_content_fixture_and_ignores_all_metadata(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    fixture_path = resources.files("nonsul_review").joinpath("demo", "cases.json")
    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    problem = Problem.model_validate(data["problem"])
    case = data["cases"][0]
    answer = Answer(
        id="arbitrary-id",
        problem=problem.id,
        source="learner",
        body=case["body"],
        error_types=["arbitrary-label"],
        split="test",
        is_alternative=True,
    )
    result, raw = run_review(problem, answer, "rubric", RunConfig(demo=True))
    assert result.meta["provider"] == "demo"
    assert result.meta["is_demo"] is True
    assert result.meta["model"] == "demo-fixture"
    assert raw["is_demo"] is True
    assert result.answer_id == "arbitrary-id"
    assert result.overall_feedback == case["overall_feedback"]
    assert all(
        attempt["response"]["id"].startswith("demo-")
        for stage in raw["stages"]
        for attempt in stage["attempts"]
    )
    altered_body = answer.model_copy(update={"body": answer.body + "\n새 문장"})
    with pytest.raises(PipelineError, match="exact bundled example answer"):
        run_review(problem, altered_body, "rubric", RunConfig(demo=True))
    changed_problem = problem.model_copy(update={"title": problem.title + " 수정"})
    with pytest.raises(PipelineError, match="exact bundled example problem"):
        run_review(changed_problem, answer, "rubric", RunConfig(demo=True))


def test_demo_rejects_provider_injection_instead_of_mislabeling_live_output(source):
    problem, answer = source
    with pytest.raises(PipelineError, match="custom provider"):
        run_review(problem, answer, "free", RunConfig(demo=True), ProcedureClient())


def test_problem_mismatch_is_rejected_before_provider_call(source):
    problem, answer = source
    answer = answer.model_copy(update={"problem": "different"})
    client = ProcedureClient()
    with pytest.raises(ValueError, match="does not match"):
        run_review(problem, answer, "rubric", RunConfig(model="test-model"), client)
    assert client.calls == []
