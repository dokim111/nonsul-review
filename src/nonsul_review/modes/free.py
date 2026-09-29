"""Holistic feedback first, followed by the shared judgement table."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nonsul_review.llm import ReviewSession
    from nonsul_review.schema import JudgementItem


def run(session: ReviewSession) -> tuple[list[JudgementItem], str]:
    """Preserve the original holistic feedback alongside the subsequent table."""
    feedback = session.call_json(
        stage="free:feedback",
        prompt_file="free-feedback-v1.txt",
        workflow={},
        validator=session.validate_feedback,
    )
    items = session.call_json(
        stage="free:table",
        prompt_file="free-table-v1.txt",
        workflow={"overall_feedback": feedback},
        validator=session.validate_table,
    )
    return items, feedback
