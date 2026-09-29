"""Holistic feedback first, followed by the shared judgement table."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nonsul_review.llm import ReviewSession
    from nonsul_review.schema import JudgementItem, ReviewFlag


def run(session: ReviewSession) -> tuple[list[JudgementItem], str, list[ReviewFlag]]:
    """Preserve the original holistic feedback alongside the subsequent table.

    The table stage cannot replace the earlier feedback. With a prompt set that
    supports review flags, it reports errors or contradictions it finds in that
    feedback so that the result is routed to instructor review.
    """
    feedback_file, table_file = session.files["free"]
    feedback = session.call_json(
        stage="free:feedback",
        prompt_file=feedback_file,
        workflow={},
        validator=session.validate_feedback,
    )
    if session.files["review_flags"]:
        items, flags = session.call_json(
            stage="free:table",
            prompt_file=table_file,
            workflow={"overall_feedback": feedback},
            validator=session.validate_table_with_flags,
        )
        return items, feedback, flags
    items = session.call_json(
        stage="free:table",
        prompt_file=table_file,
        workflow={"overall_feedback": feedback},
        validator=session.validate_table,
    )
    return items, feedback, []
