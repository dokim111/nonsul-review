"""The human review trail must survive edits, reordering, and common mistakes."""

from __future__ import annotations

import hashlib
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import pytest

from nonsul_review.io import canonical_hash, read_json, read_yaml, write_json, write_yaml
from nonsul_review.review import ReviewError, export_review, finalize_review


@pytest.fixture
def result_path(tmp_path: Path) -> Path:
    result = {
        "answer_id": "ex-001-a01",
        "problem_id": "ex-001",
        "mode": "rubric",
        "items": [
            {
                "rubric_id": "R1",
                "verdict": "met",
                "evidence": "f(0) = 0",
                "reason": "The boundary condition is used.",
                "feedback": "The first step is clear.",
                "needs_review": False,
            },
            {
                "rubric_id": "R2",
                "verdict": "partial",
                "evidence": "Therefore f(x) = x.",
                "reason": "The conclusion needs a justification.",
                "feedback": "Explain the final implication.",
                "needs_review": False,
            },
        ],
        "overall_feedback": "Check the final implication.",
        "meta": {
            "model": "fixture-no-api",
            "prompt_version": "rubric-v1",
            "temperature": 0,
            "created_at": "2026-09-29T07:40:00+09:00",
        },
    }
    path = tmp_path / "results" / "ex-001-a01.rubric.json"
    write_json(path, result)
    return path


def _save_review(path: Path, review: dict) -> None:
    write_yaml(path, review, overwrite=True)


def test_no_edit_round_trip_preserves_original_and_marks_explicit_finalization(result_path: Path):
    original_bytes = result_path.read_bytes()
    review_path = export_review(result_path)
    review = read_yaml(review_path)
    assert not Path(review["source"]["path"]).is_absolute()
    assert review["source"]["sha256"] == hashlib.sha256(original_bytes).hexdigest()
    assert review["items"][0]["model_evidence"] == "f(0) = 0"
    final_path, changes_path = finalize_review(review_path)
    final, changes = read_json(final_path), read_json(changes_path)
    assert result_path.read_bytes() == original_bytes
    assert final["status"] == "finalized"
    assert datetime.fromisoformat(final["finalized_at"]).utcoffset() is not None
    assert final["meta"] == read_json(result_path)["meta"]
    assert changes["summary"]["content_changed_item_count"] == 0
    assert changes["summary"]["edit_levels"]["unrated"] == 2
    assert all(item["changes"] == {} for item in changes["items"])
    assert all(item["edit_level"] is None for item in final["items"])
    assert "score" not in final


def test_all_manual_fields_are_tracked_and_item_reordering_is_safe(result_path: Path):
    before = result_path.read_bytes()
    review_path = export_review(result_path)
    review = read_yaml(review_path)
    second = review["items"][1]
    second.update(
        verdict="met",
        evidence="The preceding equality implies f(x) = x.",
        reason="The previous line establishes the needed implication.",
        feedback="The conclusion follows from your previous line.",
        edit_level="major",
    )
    review["items"][0]["edit_level"] = "none"
    review["items"].reverse()
    review["overall_feedback"] = "Both steps are justified."
    _save_review(review_path, review)
    final_path, changes_path = finalize_review(review_path)
    final, changes = read_json(final_path), read_json(changes_path)
    assert [item["rubric_id"] for item in final["items"]] == ["R1", "R2"]
    assert final["items"][1]["verdict"] == "met"
    log = changes["items"][1]
    assert log["changes"]["verdict"] == {"before": "partial", "after": "met"}
    assert set(log["changes"]) == {"verdict", "evidence", "reason", "feedback", "edit_level"}
    assert changes["summary"]["content_changed_item_count"] == 1
    assert changes["summary"]["verdict_changed_count"] == 1
    assert changes["summary"]["feedback_changed_count"] == 1
    assert changes["summary"]["overall_feedback_changed"] is True
    assert result_path.read_bytes() == before


def test_edit_level_can_be_absent(result_path: Path):
    review_path = export_review(result_path)
    review = read_yaml(review_path)
    for item in review["items"]:
        item.pop("edit_level")
    _save_review(review_path, review)
    final_path, _ = finalize_review(review_path)
    assert all(item["edit_level"] is None for item in read_json(final_path)["items"])


@pytest.mark.parametrize("mutation", ["unknown", "drop", "duplicate"])
def test_rejects_incomplete_or_different_rubric_item_sets(result_path: Path, mutation: str):
    review_path = export_review(result_path)
    review = read_yaml(review_path)
    if mutation == "unknown":
        review["items"][0]["rubric_id"] = "R999"
    elif mutation == "drop":
        review["items"].pop()
    else:
        review["items"].append(deepcopy(review["items"][0]))
    _save_review(review_path, review)
    with pytest.raises(ReviewError):
        finalize_review(review_path)
    assert not list(result_path.parent.glob("*.final.json"))
    assert not list(result_path.parent.glob("*.changes.json"))


def test_embedded_baseline_cannot_be_forged_even_if_its_hash_is_updated(result_path: Path):
    review_path = export_review(result_path)
    review = read_yaml(review_path)
    review["original"]["items"][1]["verdict"] = "met"
    review["source"]["canonical_sha256"] = canonical_hash(review["original"])
    review["items"][1]["model_verdict"] = "met"
    review["items"][1]["verdict"] = "met"
    _save_review(review_path, review)
    with pytest.raises(ReviewError, match="baseline"):
        finalize_review(review_path)


def test_external_original_changes_are_detected(result_path: Path):
    review_path = export_review(result_path)
    original = read_json(result_path)
    original["overall_feedback"] = "This is a different run."
    write_json(result_path, original, overwrite=True)
    modified_bytes = result_path.read_bytes()
    with pytest.raises(ReviewError, match="fingerprint"):
        finalize_review(review_path)
    assert result_path.read_bytes() == modified_bytes


def test_rejects_changed_read_only_display_and_mismatched_answer(result_path: Path):
    review_path = export_review(result_path)
    clean = read_yaml(review_path)
    altered = deepcopy(clean)
    altered["items"][0]["model_evidence"] = "Invented quotation."
    _save_review(review_path, altered)
    with pytest.raises(ReviewError, match="read-only"):
        finalize_review(review_path)
    altered = deepcopy(clean)
    altered["answer_id"] = "another-answer"
    _save_review(review_path, altered)
    with pytest.raises(ReviewError, match="identifiers"):
        finalize_review(review_path)


def test_unresolved_model_judgment_requires_instructor_resolution(result_path: Path):
    source = read_json(result_path)
    source["items"][0].update(verdict=None, needs_review=True, reason="Cannot determine.")
    write_json(result_path, source, overwrite=True)
    review_path = export_review(result_path)
    with pytest.raises(ReviewError, match="unresolved"):
        finalize_review(review_path)
    review = read_yaml(review_path)
    review["items"][0].update(verdict="met", reason="Verified against the actual equality.")
    _save_review(review_path, review)
    final_path, changes_path = finalize_review(review_path)
    final, changes = read_json(final_path), read_json(changes_path)
    assert final["items"][0]["needs_review"] is False
    assert changes["items"][0]["changes"]["verdict"] == {"before": None, "after": "met"}
    assert changes["summary"]["review_flags_resolved_count"] == 1


@pytest.mark.parametrize("invalid", ["automatic", 1, [], {}])
def test_invalid_edit_levels_are_not_silently_ignored(result_path: Path, invalid):
    review_path = export_review(result_path)
    review = read_yaml(review_path)
    review["items"][0]["edit_level"] = invalid
    _save_review(review_path, review)
    with pytest.raises(ReviewError, match="Edit level"):
        finalize_review(review_path)


@pytest.mark.parametrize("field", ["reason", "evidence", "overall_feedback"])
def test_final_edits_still_satisfy_the_common_judgment_schema(result_path: Path, field: str):
    review_path = export_review(result_path)
    review = read_yaml(review_path)
    if field == "overall_feedback":
        review[field] = ""
    else:
        review["items"][0][field] = ""
    _save_review(review_path, review)
    with pytest.raises(ReviewError, match="result schema"):
        finalize_review(review_path)
    assert not list(result_path.parent.glob("*.final.json"))


def test_exports_and_finalization_never_overwrite_existing_artifacts(result_path: Path):
    review_path = export_review(result_path)
    review_bytes = review_path.read_bytes()
    with pytest.raises(FileExistsError):
        export_review(result_path)
    assert review_path.read_bytes() == review_bytes
    final_path, changes_path = finalize_review(review_path)
    final_bytes, change_bytes = final_path.read_bytes(), changes_path.read_bytes()
    with pytest.raises(FileExistsError):
        finalize_review(review_path)
    assert final_path.read_bytes() == final_bytes
    assert changes_path.read_bytes() == change_bytes


def test_existing_change_log_prevents_partial_finalization(result_path: Path):
    review_path = export_review(result_path)
    changes_path = result_path.with_name(f"{result_path.stem}.changes.json")
    changes_path.write_text("existing audit log", encoding="utf-8")
    with pytest.raises(FileExistsError):
        finalize_review(review_path)
    assert changes_path.read_text(encoding="utf-8") == "existing audit log"
    assert not result_path.with_name(f"{result_path.stem}.final.json").exists()


def test_final_write_failure_rolls_back_only_new_audit_log(result_path: Path, monkeypatch):
    from nonsul_review import review as module

    review_path = export_review(result_path)
    real_write_json = module.write_json

    def write_with_failure(path, data, **kwargs):
        if str(path).endswith(".final.json"):
            raise OSError("simulated full disk")
        return real_write_json(path, data, **kwargs)

    monkeypatch.setattr(module, "write_json", write_with_failure)
    with pytest.raises(OSError):
        finalize_review(review_path)
    assert not result_path.with_name(f"{result_path.stem}.final.json").exists()
    assert not result_path.with_name(f"{result_path.stem}.changes.json").exists()


def test_bundle_can_move_while_keeping_relative_source_reference(result_path: Path, tmp_path: Path):
    import shutil

    review_path = export_review(result_path, result_path.parent / "reviews" / "teacher.yaml")
    target = tmp_path / "moved-bundle"
    shutil.copytree(result_path.parent, target)
    result_path.unlink()
    final_path, _ = finalize_review(target / "reviews" / review_path.name, target / "finals")
    assert final_path.parent == target / "finals"
    assert read_json(final_path)["answer_id"] == "ex-001-a01"
