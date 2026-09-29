"""Item-by-item judgement followed by feedback synthesis."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nonsul_review.llm import ReviewSession
    from nonsul_review.schema import JudgementItem


def run(session: ReviewSession) -> tuple[list[JudgementItem], str]:
    """Keep each rubric call independent; synthesize only after all are valid."""
    items = []
    for rubric in session.problem.rubric:
        target_id = rubric.id
        item = session.call_json(
            stage=f"rubric:{target_id}",
            prompt_file="rubric-item-v1.txt",
            workflow={"target_rubric_id": target_id},
            validator=lambda data, target=target_id: session.validate_items(
                [data], expected_ids=[target]
            )[0],
        )
        items.append(item)

    feedback = session.call_json(
        stage="rubric:feedback",
        prompt_file="rubric-feedback-v1.txt",
        workflow={"judgements": [item.model_dump(mode="json") for item in items]},
        validator=session.validate_feedback,
    )
    return items, feedback
