"""Offline tests for experiments/jev (no network, no API key)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JEV = ROOT / "experiments" / "jev"
if not JEV.is_dir():  # source distributions do not ship the experiment
    pytest.skip("experiments/jev is not present", allow_module_level=True)
sys.path.insert(0, str(JEV))

import jev_analyze  # noqa: E402
import jev_common as jc  # noqa: E402
import jev_run  # noqa: E402

EX3 = ROOT / "examples" / "ex-003"
GOLD = {
    "ex-003-a01": ["met", "met", "met", "met"],
    "ex-003-a02": ["met", "met", "partial", "partial"],
    "ex-003-a03": ["met", "met", "met", "met"],
}


def _case(answer: str = "a02") -> jc.Case:
    cases = jc.discover_cases([EX3], [EX3 / "answers" / f"{answer}.md"])
    assert len(cases) == 1
    return cases[0]


def _requirements():
    case = _case()
    return jc.load_requirements(JEV / "requirements" / "ex-003.yaml", case.problem)


def test_state_carries_the_same_sources_as_opus():
    case = _case()
    state = jc.build_state(case.problem, case.answer)
    assert state["student_answer"] == case.answer.body
    assert state["model_answer"] == case.problem.model_answer
    assert [r["id"] for r in state["rubric"]] == ["R1", "R2", "R3", "R4"]


@pytest.mark.parametrize("variant", ["A", "B", "C"])
def test_request_shapes(variant):
    case = _case()
    reqs = _requirements() if variant == "C" else None
    body = jc.build_request(case, variant, "ko", "jev-test", reqs)
    assert body["model"] == "jev-test"
    questions = body["questions"]
    if variant == "A":
        assert set(questions) == {"R1", "R2", "R3", "R4"}
        assert set(questions["R3"]["criteria"]) == {"met", "partial", "not_met"}
        assert questions["R3"]["type"] == "choice"
    elif variant == "B":
        assert questions["R3"]["type"] == "score"
        assert [c.split(":")[0] for c in questions["R3"]["criteria"]] == [
            "not_met",
            "partial",
            "met",
        ]
    else:
        assert "R3__R3.limit" in questions
        assert all(q["type"] == "noul" for q in questions.values())


def test_english_questions_differ_only_in_language():
    case = _case()
    ko = jc.questions_variant_a(case.problem, "ko")["R3"]["instructions"]
    en = jc.questions_variant_a(case.problem, "en")["R3"]["instructions"]
    assert (
        ko != en and case.problem.rubric[2].criteria in ko and case.problem.rubric[2].criteria in en
    )


def test_requirements_are_validated(tmp_path):
    case = _case()
    bad = tmp_path / "ex-003.yaml"
    bad.write_text("problem_id: ex-003\nrubric:\n  R1: []\n", encoding="utf-8")
    with pytest.raises(ValueError):
        jc.load_requirements(bad, case.problem)


def test_readers_and_aggregation():
    verdict, conf, _ = jc.read_choice(
        {
            "choice": "partial",
            "confidence": 0.8,
            "probabilities": {"met": 0.1, "partial": 0.8, "not_met": 0.1},
        }
    )
    assert (verdict, conf) == ("partial", 0.8)
    for broken in (
        {"choice": "met", "confidence": 0.9, "probabilities": {"met": 0.9, "partial": 0.1}},
        {
            "choice": "met",
            "confidence": 0.9,
            "probabilities": {"met": 0.9, "partial": 0.3, "not_met": 0.1},
        },
        {
            "choice": "met",
            "confidence": float("nan"),
            "probabilities": {"met": 1, "partial": 0, "not_met": 0},
        },
    ):
        with pytest.raises(jc.ResponseShapeError):
            jc.read_choice(broken)
    verdict, conf, probs = jc.read_score(
        {"score": 1.2, "confidence": 0.6, "probabilities": {"0": 0.1, "1": 0.6, "2": 0.3}}
    )
    assert verdict == "partial" and probs["met"] == 0.3
    assert jc.read_noul({"type": "noul", "noul": 0.99}) == 0.99  # documented shape
    assert jc.read_noul({"probability": 0.7}) == 0.7
    with pytest.raises(jc.ResponseShapeError):
        jc.read_noul({"noul": 1.3})
    with pytest.raises(jc.ResponseShapeError):
        jc.read_noul({"unknown": 1})
    reqs = [{"id": "x.b", "role": "base"}, {"id": "x.f", "role": "full"}]
    assert jc.aggregate_requirements(reqs, {"x.b": 0.9, "x.f": 0.8})[0] == "met"
    assert jc.aggregate_requirements(reqs, {"x.b": 0.9, "x.f": 0.2})[0] == "partial"
    verdict, conf = jc.aggregate_requirements(reqs, {"x.b": 0.3, "x.f": 0.9})
    assert verdict == "not_met" and conf == pytest.approx(0.7)


def _scripted_transport(verdicts: dict[str, list[str]], confidence: float = 0.95, fail_first=0):
    calls = {"n": 0}

    def transport(endpoint, key, body, timeout):
        calls["n"] += 1
        if calls["n"] <= fail_first:
            return 503, {"error": "busy"}
        answer = body["state"]["student_answer"]
        aid = next(a for a in verdicts if _case(a.split("-")[-1]).answer.body == answer)
        answers = {
            rid: {
                "choice": v,
                "confidence": confidence,
                "probabilities": {
                    x: (confidence if x == v else (1 - confidence) / 2) for x in jc.VERDICTS
                },
            }
            for rid, v in zip(["R1", "R2", "R3", "R4"], verdicts[aid])
        }
        return 200, {"answers": answers}

    transport.calls = calls
    return transport


def _args(tmp_path, *extra):
    return [
        "--problems",
        str(EX3),
        "--answers",
        str(EX3 / "answers"),
        "--variant",
        "A",
        "--out",
        str(tmp_path / "runs"),
        "--sleep",
        "0",
        *extra,
    ]


def test_dry_run_sends_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)

    def refuse(*_):
        raise AssertionError("network used in dry run")

    assert jev_run.run(_args(tmp_path, "--dry-run"), transport=refuse) == 0
    written = sorted((tmp_path / "runs" / "variant-A" / "ko").glob("*.request.json"))
    assert [p.name for p in written] == [f"ex-003-a0{i}.request.json" for i in (1, 2, 3)]


def test_run_requires_key_and_blocks_learner_answers(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        jev_run.run(_args(tmp_path))
    learner = tmp_path / "answers"
    learner.mkdir()
    text = (EX3 / "answers" / "a01.md").read_text(encoding="utf-8")
    (learner / "a01.md").write_text(text.replace("source: synthetic", "source: learner"), "utf-8")
    monkeypatch.setenv("OPENCODE_API_KEY", "test")
    with pytest.raises(SystemExit):
        jev_run.run(
            [
                "--problems",
                str(EX3),
                "--answers",
                str(learner),
                "--variant",
                "A",
                "--out",
                str(tmp_path / "x"),
            ]
        )


def test_run_retries_resumes_and_respects_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", "test")
    monkeypatch.setattr(jev_run.time, "sleep", lambda *_: None)
    transport = _scripted_transport(GOLD, fail_first=1)
    assert jev_run.run(_args(tmp_path, "--repeat", "2"), transport=transport) == 0
    files = sorted((tmp_path / "runs" / "variant-A" / "ko").glob("*.r*.json"))
    assert len(files) == 6
    meta = json.loads(files[0].read_text(encoding="utf-8"))["meta"]
    assert meta["attempts"] == 2 and meta["http_status"] == 200 and meta["request_sha256"]
    # Existing results are skipped, so a second pass sends nothing.
    before = transport.calls["n"]
    assert jev_run.run(_args(tmp_path, "--repeat", "2"), transport=transport) == 0
    assert transport.calls["n"] == before
    # A hard cap stops a fresh run.
    assert (
        jev_run.run(
            _args(tmp_path / "b", "--repeat", "3", "--max-requests", "2"), transport=transport
        )
        == 2
    )


def test_analysis_separates_sets_and_counts_confident_false_met(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", "test")
    monkeypatch.setattr(jev_run.time, "sleep", lambda *_: None)
    wrong = dict(GOLD)
    wrong["ex-003-a02"] = ["met", "met", "met", "partial"]  # the trap, confidently wrong
    assert jev_run.run(_args(tmp_path), transport=_scripted_transport(wrong, 0.95)) == 0
    gold_dir = tmp_path / "gold"
    gold_dir.mkdir()
    for aid, verdicts in GOLD.items():
        items = "".join(
            f"- rubric_id: R{i + 1}\n  verdict: {v}\n  unresolved: false\n"
            for i, v in enumerate(verdicts)
        )
        (gold_dir / f"{aid}.yaml").write_text(
            f"answer_id: {aid}\nproblem_id: ex-003\nitems:\n{items}", encoding="utf-8"
        )
    sets = tmp_path / "sets.yaml"
    sets.write_text(
        "normal: [ex-003-a01]\nrisk: [ex-003-a02]\nalternative: [ex-003-a03]\n", "utf-8"
    )
    out = tmp_path / "report"
    code = jev_analyze.main(
        [
            "--runs",
            str(tmp_path / "runs"),
            "--problems",
            str(EX3),
            "--answers",
            str(EX3 / "answers"),
            "--gold",
            str(gold_dir),
            "--sets",
            str(sets),
            "--criteria",
            str(JEV / "criteria.yaml"),
            "--out",
            str(out),
        ]
    )
    assert code == 0
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))["report"][
        "variant-A/ko/jev-1.13-free"
    ]
    risk = report["sets"]["risk"]
    tau90 = next(p for p in risk["curve"] if p["tau"] == 0.9)
    assert tau90["high_confidence_false_met"] == 1
    assert report["sets"]["alternative"]["wrongly_deducted_share"] == 0
    assert set(report["sets"]) == {"normal", "risk", "alternative"}
    text = (out / "report.md").read_text(encoding="utf-8")
    assert "risk: high-confidence false met <= 0 -> FAIL" in text
    assert "normal: coverage >= 0.6 -> PASS" in text


def test_zen_is_the_free_default_and_paid_models_need_confirmation(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", "test")
    monkeypatch.setattr(jev_run.time, "sleep", lambda *_: None)
    seen = {}

    def transport(endpoint, key, body, timeout):
        seen.update(endpoint=endpoint, model=body["model"])
        return _scripted_transport(GOLD)(endpoint, key, body, timeout)

    assert jev_run.run(_args(tmp_path, "--probe"), transport=transport) == 0
    assert seen == {"endpoint": "https://opencode.ai/zen/v1/systemone", "model": "jev-1.13-free"}
    with pytest.raises(SystemExit):
        jev_run.run(_args(tmp_path, "--model", "jev-1.13"), transport=transport)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    with pytest.raises(SystemExit):
        jev_run.run(_args(tmp_path, "--provider", "typesafe"), transport=transport)
    assert (
        jev_run.run(
            _args(tmp_path / "paid", "--provider", "typesafe", "--allow-paid"), transport=transport
        )
        == 0
    )


def test_free_period_end_stops_without_retrying_paid(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", "test")
    calls = []

    def gone(endpoint, key, body, timeout):
        calls.append(body["model"])
        return 410, {"error": "model retired"}

    assert jev_run.run(_args(tmp_path), transport=gone) == 3
    assert calls == ["jev-1.13-free"]
    assert list((tmp_path / "runs").rglob("*.failed.json"))


def test_cost_and_usage_are_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", "test")
    monkeypatch.setattr(jev_run.time, "sleep", lambda *_: None)
    base = _scripted_transport(GOLD)

    def priced(endpoint, key, body, timeout):
        status, payload = base(endpoint, key, body, timeout)
        return status, {**payload, "usage": {"input_tokens": 1200}, "cost": 0}

    assert jev_run.run(_args(tmp_path), transport=priced) == 0
    record = json.loads(next((tmp_path / "runs").rglob("*.r0.json")).read_text(encoding="utf-8"))
    assert record["meta"]["cost"] == 0 and record["meta"]["usage"]["input_tokens"] == 1200
    assert record["meta"]["provider"] == "zen"


def _analyze(tmp_path, answers=None, opus=None):
    gold_dir = tmp_path / "gold"
    gold_dir.mkdir(exist_ok=True)
    for aid, verdicts in GOLD.items():
        items = "".join(
            f"- rubric_id: R{i + 1}\n  verdict: {v}\n  unresolved: false\n"
            for i, v in enumerate(verdicts)
        )
        (gold_dir / f"{aid}.yaml").write_text(
            f"answer_id: {aid}\nproblem_id: ex-003\nitems:\n{items}", encoding="utf-8"
        )
    out = tmp_path / "report"
    argv = ["--runs", str(tmp_path / "runs"), "--problems", str(EX3)]
    argv += ["--answers", str(answers or EX3 / "answers"), "--gold", str(gold_dir)]
    argv += ["--out", str(out)]
    if opus:
        argv += ["--opus", str(opus)]
    assert jev_analyze.main(argv) == 0
    data = json.loads((out / "report.json").read_text(encoding="utf-8"))
    return data, (out / "report.md").read_text(encoding="utf-8")


def test_positive_cost_on_free_model_stops_the_run(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", "test")
    base = _scripted_transport(GOLD)
    calls = []

    def charged(endpoint, key, body, timeout):
        calls.append(1)
        status, payload = base(endpoint, key, body, timeout)
        return status, {**payload, "cost": 0.01}

    assert jev_run.run(_args(tmp_path), transport=charged) == 4
    assert len(calls) == 1
    record = json.loads(next((tmp_path / "runs").rglob("*.r0.json")).read_text(encoding="utf-8"))
    assert record["meta"]["cost_status"] == "charged"


def test_missing_cost_is_unknown_not_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", "test")
    monkeypatch.setattr(jev_run.time, "sleep", lambda *_: None)
    assert jev_run.run(_args(tmp_path), transport=_scripted_transport(GOLD)) == 0
    record = json.loads(next((tmp_path / "runs").rglob("*.r0.json")).read_text(encoding="utf-8"))
    assert record["meta"]["cost_status"] == "unknown" and record["meta"]["cost"] is None


def test_request_cap_counts_retries(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", "test")
    monkeypatch.setattr(jev_run.time, "sleep", lambda *_: None)
    calls = []

    def busy(endpoint, key, body, timeout):
        calls.append(1)
        return 503, {"error": "busy"}

    jev_run.run(_args(tmp_path, "--max-requests", "1", "--retries", "2"), transport=busy)
    assert len(calls) == 1


def test_resume_refuses_results_from_a_different_request(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", "test")
    monkeypatch.setattr(jev_run.time, "sleep", lambda *_: None)
    transport = _scripted_transport(GOLD)
    assert jev_run.run(_args(tmp_path), transport=transport) == 0
    before = transport.calls["n"]
    changed = _args(tmp_path, "--model", "jev-1.13", "--allow-paid")
    assert jev_run.run(changed, transport=transport) == 5
    assert jev_run.run(_args(tmp_path, "--lang", "en"), transport=transport) == 0  # other folder
    assert transport.calls["n"] == before + 3


def test_missing_items_stay_in_the_denominator(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", "test")
    monkeypatch.setattr(jev_run.time, "sleep", lambda *_: None)
    base = _scripted_transport(GOLD)

    def partial_reply(endpoint, key, body, timeout):
        status, payload = base(endpoint, key, body, timeout)
        return status, {"answers": {"R1": payload["answers"]["R1"]}}

    assert jev_run.run(_args(tmp_path), transport=partial_reply) == 0
    data, text = _analyze(tmp_path)
    summary = data["report"]["variant-A/ko/jev-1.13-free"]["sets"]["design"]
    assert summary["items"] == 12 and summary["undecided"] == 9 and summary["accuracy"] == 0.25
    assert data["report"]["variant-A/ko/jev-1.13-free"]["requests"] == {"incomplete": 3}
    assert "item missing from response" in text


def test_changed_inputs_and_mismatched_opus_results_are_excluded(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", "test")
    monkeypatch.setattr(jev_run.time, "sleep", lambda *_: None)
    answers = tmp_path / "answers"
    answers.mkdir()
    for f in (EX3 / "answers").glob("a0*.md"):
        (answers / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    args = _args(tmp_path)
    args[args.index(str(EX3 / "answers"))] = str(answers)
    assert jev_run.run(args, transport=_scripted_transport(GOLD)) == 0
    opus = tmp_path / "opus"
    opus.mkdir()
    (opus / "ex-003-a01.rubric.json").write_text(
        json.dumps(
            {
                "answer_id": "ex-003-a01",
                "mode": "rubric",
                "items": [{"rubric_id": "R1", "verdict": "met"}],
                "meta": {"answer_body_sha256": "not-the-same-body"},
            }
        ),
        encoding="utf-8",
    )
    target = answers / "a03.md"
    target.write_text(target.read_text(encoding="utf-8") + "\n추가 문장.", encoding="utf-8")
    data, _ = _analyze(tmp_path, answers=answers, opus=opus)
    block = data["report"]["variant-A/ko/jev-1.13-free"]
    assert block["sets"]["design"]["items"] == 8  # a03 excluded as stale
    assert any("changed since this run" in s for s in data["stale"])
    assert block["versus_opus"] == {"compared": 0, "skipped_answer_mismatch": 1}


def test_ex002_r1_decomposition_matches_the_rubric_partial_boundary():
    case = jc.discover_cases(
        [ROOT / "examples" / "ex-002"], [ROOT / "examples" / "ex-002" / "answers"]
    )[0]
    reqs = jc.load_requirements(JEV / "requirements" / "ex-002.yaml", case.problem)["R1"]
    one_initial_wrong = {
        "R1.recurrence": 0.9,
        "R1.initial_any": 0.9,
        "R1.initial_both": 0.1,
        "R1.justify": 0.9,
    }
    both_wrong = {**one_initial_wrong, "R1.initial_any": 0.1}
    assert jc.aggregate_requirements(reqs, one_initial_wrong)[0] == "partial"
    assert jc.aggregate_requirements(reqs, both_wrong)[0] == "not_met"
