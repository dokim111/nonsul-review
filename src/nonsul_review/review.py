"""Editable instructor review copies and traceable, explicit finalization.

The model JSON is the source of truth and is never written by this module.
The YAML includes a readable baseline, but finalization also reloads the
original file and checks its fingerprint.  Editing the YAML baseline alone
therefore cannot erase or disguise a correction.
"""

from __future__ import annotations

import hashlib
import os
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .io import MAX_FILE_BYTES, canonical_hash, read_json, read_yaml, write_json, write_yaml
from .schema import Result

REVIEW_FORMAT = "nonsul-review-v1"
EDITABLE_FIELDS = ("verdict", "evidence", "reason", "feedback")
TEXT_FIELDS = ("evidence", "reason", "feedback")
VERDICTS = frozenset({"met", "partial", "not_met"})
EDIT_LEVELS = frozenset({"none", "minor", "major"})
_ROOT_KEYS = frozenset(
    {
        "review_format",
        "instructions",
        "answer_id",
        "problem_id",
        "mode",
        "source",
        "original",
        "model_overall_feedback",
        "overall_feedback",
        "items",
    }
)
_ITEM_KEYS = frozenset(
    {
        "rubric_id",
        "model_verdict",
        "model_evidence",
        "model_reason",
        "model_feedback",
        "model_needs_review",
        "model_review_flags",
        *EDITABLE_FIELDS,
        "edit_level",
    }
)


class ReviewError(ValueError):
    """A review cannot be safely exported or finalized."""


def _flag_notes(result: dict[str, Any], rubric_id: str) -> list[str]:
    """Read-only notes raised by the final model stage for one rubric item."""
    return [
        flag["note"] for flag in result.get("review_flags", []) if flag["rubric_id"] == rubric_id
    ]


def _relative_path(path: Path, parent: Path) -> str:
    """Keep bundles portable, falling back to absolute paths across volumes."""
    try:
        return Path(os.path.relpath(path, parent)).as_posix()
    except ValueError:  # Different Windows drive letters.
        return str(path)


def _read_bytes(path: Path, label: str) -> bytes:
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_FILE_BYTES + 1)
    except OSError:
        raise ReviewError(f"Cannot read the {label} file.") from None
    if len(raw) > MAX_FILE_BYTES:
        raise ReviewError(f"The {label} file exceeds the supported input size.")
    return raw


def _load_original(path: Path) -> tuple[dict[str, Any], dict[str, Any], str]:
    raw = _read_bytes(path, "original result")
    try:
        original = read_json(path)
        validated = Result.model_validate(original).model_dump(mode="json")
        if isinstance(original, dict) and "review_flags" not in original:
            # v1 records have no flag field; do not add one to their final copies.
            validated.pop("review_flags", None)
    except Exception:
        # Validation errors can echo answer excerpts and metadata.  Do not
        # include their representations in a CLI-visible exception.
        raise ReviewError("The original result does not match the result schema.") from None
    if _read_bytes(path, "original result") != raw:
        raise ReviewError("The original result changed while being read; retry.")
    if not isinstance(original, dict):
        raise ReviewError("The original result must be a JSON object.")
    return original, validated, hashlib.sha256(raw).hexdigest()


def _load_review(path: Path) -> tuple[dict[str, Any], str]:
    raw = _read_bytes(path, "review")
    try:
        review = read_yaml(path)
    except Exception:
        raise ReviewError("The review file is not valid YAML.") from None
    if _read_bytes(path, "review") != raw:
        raise ReviewError("The review changed while being read; retry.")
    if not isinstance(review, dict):
        raise ReviewError("The review file must contain a YAML mapping.")
    return review, hashlib.sha256(raw).hexdigest()


def export_review(result_path: Path, out_path: Path | None = None) -> Path:
    """Create a non-overwriting YAML copy for an instructor to edit.

    In each item, ``model_*`` fields are a read-only display of the original
    judgment.  ``verdict``, ``evidence``, ``reason`` and ``feedback`` can be
    edited.  ``edit_level`` may be absent or null.  Item order is immaterial.
    """
    result_path = Path(result_path).expanduser().resolve()
    if out_path is None:
        out_path = result_path.with_name(f"{result_path.stem}.review.yaml")
    out_path = Path(out_path).expanduser().resolve()
    original, result, fingerprint = _load_original(result_path)

    items = []
    for item in result["items"]:
        items.append(
            {
                "rubric_id": item["rubric_id"],
                "model_verdict": item["verdict"],
                "model_evidence": item["evidence"],
                "model_reason": item["reason"],
                "model_feedback": item["feedback"],
                "model_needs_review": item.get("needs_review", False),
                "model_review_flags": _flag_notes(result, item["rubric_id"]),
                **{field: item[field] for field in EDITABLE_FIELDS},
                "edit_level": None,
            }
        )
    review = {
        "review_format": REVIEW_FORMAT,
        "instructions": (
            "강사가 항목별 verdict, evidence, reason, feedback 및 전체 "
            "overall_feedback을 검토·수정하세요. edit_level은 "
            "none / minor / major 또는 null(미기재)입니다. model_* 및 "
            "source, original, 식별자는 수정하지 마세요. model_review_flags는 "
            "모델의 마지막 단계가 남긴 강사 확인 요청이며, 해당 항목과 "
            "overall_feedback을 확인한 뒤 확정하세요. null 판정은 반드시 "
            "해결해야 합니다. finalize 명령 실행은 모든 항목을 강사가 "
            "확인하여 확정한다는 의미이며 점수를 자동 산출하지 않습니다. "
            "원본 JSON은 보존하고, 검수 파일과 함께 이동할 때에는 "
            "source.path의 상대 위치도 유지하세요."
        ),
        "answer_id": result["answer_id"],
        "problem_id": result["problem_id"],
        "mode": result["mode"],
        "source": {
            "path": _relative_path(result_path, out_path.parent),
            "sha256": fingerprint,
            "canonical_sha256": canonical_hash(original),
        },
        "original": deepcopy(original),
        "model_overall_feedback": result["overall_feedback"],
        "overall_feedback": result["overall_feedback"],
        "items": items,
    }
    write_yaml(out_path, review, overwrite=False)
    return out_path


def _check_baseline(review: dict[str, Any], review_path: Path) -> tuple[Path, dict[str, Any], str]:
    if set(review) - _ROOT_KEYS:
        raise ReviewError("Review contains unsupported fields; use the documented fields.")
    if review.get("review_format") != REVIEW_FORMAT:
        raise ReviewError("Unsupported or missing review format; export a new review copy.")
    source = review.get("source")
    if (
        not isinstance(source, dict)
        or set(source) != {"path", "sha256", "canonical_sha256"}
        or not isinstance(source.get("path"), str)
        or not source["path"].strip()
    ):
        raise ReviewError("Review is missing a valid original source reference.")
    try:
        source_path = Path(source["path"]).expanduser()
        if not source_path.is_absolute():
            source_path = review_path.parent / source_path
        source_path = source_path.resolve()
    except (OSError, RuntimeError, ValueError):
        raise ReviewError("Review has an invalid original source path.") from None
    original, result, fingerprint = _load_original(source_path)
    if source.get("sha256") != fingerprint:
        raise ReviewError(
            "The original result fingerprint no longer matches. Restore the original "
            "JSON or export a fresh review copy before finalizing."
        )
    try:
        expected_canonical = canonical_hash(original)
        baseline_matches = canonical_hash(review.get("original")) == expected_canonical
    except (TypeError, ValueError, OverflowError):
        raise ReviewError("The embedded original baseline is invalid.") from None
    if source.get("canonical_sha256") != expected_canonical or not baseline_matches:
        raise ReviewError("The embedded original baseline was changed; preserve it unchanged.")
    for field in ("answer_id", "problem_id", "mode"):
        if review.get(field) != result[field]:
            raise ReviewError("Review identifiers do not match the original result.")
    if review.get("model_overall_feedback") != result["overall_feedback"]:
        raise ReviewError("Read-only model overall feedback was changed.")
    if not isinstance(review.get("overall_feedback"), str):
        raise ReviewError("Overall feedback must be a string.")
    return source_path, result, fingerprint


def _check_items(review: dict[str, Any], result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    review_items = review.get("items")
    if not isinstance(review_items, list):
        raise ReviewError("Review items must be a list.")
    originals = {item["rubric_id"]: item for item in result["items"]}
    reviewed: dict[str, dict[str, Any]] = {}
    for item in review_items:
        if not isinstance(item, dict) or set(item) - _ITEM_KEYS:
            raise ReviewError("Review item contains invalid or unsupported fields.")
        rubric_id = item.get("rubric_id")
        if not isinstance(rubric_id, str) or rubric_id not in originals:
            raise ReviewError("Review contains an unknown rubric ID.")
        if rubric_id in reviewed:
            raise ReviewError("Review contains a duplicate rubric ID.")
        original = originals[rubric_id]
        for field in EDITABLE_FIELDS:
            if f"model_{field}" not in item or item[f"model_{field}"] != original[field]:
                raise ReviewError("A read-only model judgment field was changed or removed.")
        if (
            "model_needs_review" not in item
            or type(item["model_needs_review"]) is not bool
            or item["model_needs_review"] != original.get("needs_review", False)
        ):
            raise ReviewError("The read-only model review flag was changed or removed.")
        expected_notes = _flag_notes(result, rubric_id)
        if "model_review_flags" in item:
            if item["model_review_flags"] != expected_notes:
                raise ReviewError("Read-only model review notes were changed.")
        elif expected_notes:
            # Copies exported before review notes existed are accepted only
            # when there is nothing to show.
            raise ReviewError("Read-only model review notes were removed.")
        verdict = item.get("verdict")
        if verdict is None:
            raise ReviewError(
                "An unresolved verdict remains. An instructor must set every verdict "
                "to met, partial, or not_met before invoking finalize."
            )
        if not isinstance(verdict, str) or verdict not in VERDICTS:
            raise ReviewError("Every verdict must be met, partial, or not_met.")
        if any(not isinstance(item.get(field), str) for field in TEXT_FIELDS):
            raise ReviewError("Evidence, reason, and feedback must be strings.")
        level = item.get("edit_level")
        if level is not None and (not isinstance(level, str) or level not in EDIT_LEVELS):
            raise ReviewError("Edit level must be none, minor, major, or null.")
        reviewed[rubric_id] = item
    if set(reviewed) != set(originals):
        raise ReviewError("Review must include every original rubric item exactly once.")
    return reviewed


def _write_final_pair(
    final_path: Path,
    final: dict[str, Any],
    changes_path: Path,
    changes: dict[str, Any],
) -> None:
    # Existence checks provide a useful early error.  write_json also makes an
    # exclusive atomic write, closing the race between this check and creation.
    if os.path.lexists(final_path) or os.path.lexists(changes_path):
        raise FileExistsError(
            "Finalization output already exists; choose another output directory."
        )
    # Write the log first: a visible final file always has its accompanying log.
    write_json(changes_path, changes, overwrite=False)
    created_stat = changes_path.stat()
    created_bytes = changes_path.read_bytes()
    try:
        write_json(final_path, final, overwrite=False)
    except Exception:
        # Roll back only our own unmodified output; never remove a file another
        # process has replaced or an instructor has edited in the meantime.
        try:
            if os.path.samestat(created_stat, changes_path.stat()) and (
                changes_path.read_bytes() == created_bytes
            ):
                changes_path.unlink()
        except OSError:
            pass
        raise


def finalize_review(review_path: Path, out_dir: Path | None = None) -> tuple[Path, Path]:
    """Explicitly approve a reviewed YAML and save its final JSON and change log.

    Calling this function (or the ``finalize`` command) is the human approval
    step.  A non-null verdict is required for every item.  Assessment levels
    are optional and are never inferred from the amount of text changed.
    """
    review_path = Path(review_path).expanduser().resolve()
    review, review_fingerprint = _load_review(review_path)
    source_path, result, fingerprint = _check_baseline(review, review_path)
    reviewed = _check_items(review, result)

    output_dir = Path(out_dir).expanduser().resolve() if out_dir is not None else review_path.parent
    final_path = output_dir / f"{source_path.stem}.final.json"
    changes_path = output_dir / f"{source_path.stem}.changes.json"
    finalized_at = datetime.now(timezone.utc).isoformat()
    source = {
        "result_path": _relative_path(source_path, output_dir),
        "sha256": fingerprint,
        "canonical_sha256": review["source"]["canonical_sha256"],
        "review_path": _relative_path(review_path, output_dir),
        "review_sha256": review_fingerprint,
    }
    final_items = []
    item_logs = []
    levels: Counter[str] = Counter()
    changed_counts: Counter[str] = Counter()
    content_changed_count = 0
    resolved_count = 0
    flag_count = 0
    for original in result["items"]:
        rubric_id = original["rubric_id"]
        edited = reviewed[rubric_id]
        level = edited.get("edit_level")
        levels[level if level is not None else "unrated"] += 1
        field_changes = {}
        content_changed = False
        for field in EDITABLE_FIELDS:
            if original[field] != edited[field]:
                field_changes[field] = {"before": original[field], "after": edited[field]}
                changed_counts[field] += 1
                content_changed = True
        content_changed_count += int(content_changed)
        if level is not None:
            field_changes["edit_level"] = {"before": None, "after": level}
        if original.get("needs_review", False):
            field_changes["needs_review"] = {"before": True, "after": False}
            resolved_count += 1
        final_items.append(
            {
                **deepcopy(original),
                **{field: edited[field] for field in EDITABLE_FIELDS},
                "edit_level": level,
                "needs_review": False,
            }
        )
        notes = _flag_notes(result, rubric_id)
        flag_count += len(notes)
        item_logs.append(
            {
                "rubric_id": rubric_id,
                **({"model_review_flags": notes} if notes else {}),
                "verdict_changed": original["verdict"] != edited["verdict"],
                "feedback_changed": original["feedback"] != edited["feedback"],
                "edit_level": level,
                "changes": field_changes,
            }
        )
    overall_changed = result["overall_feedback"] != review["overall_feedback"]
    final = {
        **deepcopy(result),
        "items": final_items,
        "overall_feedback": review["overall_feedback"],
        "status": "finalized",
        "finalized_at": finalized_at,
        "source": source,
    }
    try:
        # Human edits still have to satisfy the shared judgment contract.  In
        # particular, met/partial require evidence and every item needs a
        # nonblank reason.  Finalization metadata is deliberately separate.
        Result.model_validate(
            {
                **deepcopy(result),
                "items": [
                    {key: value for key, value in item.items() if key != "edit_level"}
                    for item in final_items
                ],
                "overall_feedback": review["overall_feedback"],
            }
        )
    except Exception:
        raise ReviewError(
            "Final judgments must satisfy the result schema: include a nonblank "
            "reason, evidence for met/partial, and overall feedback within the text limits."
        ) from None
    changes = {
        "schema_version": 1,
        "answer_id": result["answer_id"],
        "problem_id": result["problem_id"],
        "mode": result["mode"],
        "status": "finalized",
        "finalized_at": finalized_at,
        "source": source,
        "items": item_logs,
        "overall_feedback": {
            "changed": overall_changed,
            "before": result["overall_feedback"],
            "after": review["overall_feedback"],
        },
        "summary": {
            "item_count": len(final_items),
            "content_changed_item_count": content_changed_count,
            "verdict_changed_count": changed_counts["verdict"],
            "feedback_changed_count": changed_counts["feedback"],
            "evidence_changed_count": changed_counts["evidence"],
            "reason_changed_count": changed_counts["reason"],
            "review_flags_resolved_count": resolved_count,
            "model_review_flag_count": flag_count,
            "overall_feedback_changed": overall_changed,
            "edit_levels": {key: levels[key] for key in ("none", "minor", "major", "unrated")},
        },
    }
    _write_final_pair(final_path, final, changes_path, changes)
    return final_path, changes_path
