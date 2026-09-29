"""Item-by-item judgement followed by feedback synthesis."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nonsul_review.llm import ReviewSession
    from nonsul_review.schema import JudgementItem, ReviewFlag


def run(session: ReviewSession) -> tuple[list[JudgementItem], str, list[ReviewFlag]]:
    """Keep each rubric call independent; synthesize only after all are valid.

    With a prompt set that supports review flags, the synthesis stage may report
    problems it finds in the item judgements. It cannot rewrite them; the flags
    are returned separately and only mark items for instructor review.
    """
    item_file, feedback_file = session.files["rubric"]
    items = []
    for rubric in session.problem.rubric:
        target_id = rubric.id
        item = session.call_json(
            stage=f"rubric:{target_id}",
            prompt_file=item_file,
            workflow={"target_rubric_id": target_id},
            validator=lambda data, target=target_id: session.validate_items(
                [data], expected_ids=[target]
            )[0],
        )
        items.append(item)

    workflow = {"judgements": [item.model_dump(mode="json") for item in items]}
    if session.files["review_flags"]:
        feedback, flags = session.call_json(
            stage="rubric:feedback",
            prompt_file=feedback_file,
            workflow=workflow,
            validator=session.validate_feedback_with_flags,
        )
        return items, feedback, flags
    feedback = session.call_json(
        stage="rubric:feedback",
        prompt_file=feedback_file,
        workflow=workflow,
        validator=session.validate_feedback,
    )
    return items, feedback, []
