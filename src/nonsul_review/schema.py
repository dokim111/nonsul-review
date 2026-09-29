"""Validated file contracts. Labels/metadata never form part of model inputs."""

from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Verdict = Literal["met", "partial", "not_met"]
Mode = Literal["rubric", "free"]
EditLevel = Literal["none", "minor", "major"]
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def validate_id(value: str) -> str:
    """IDs double as file stems; reject separators, traversal and unsafe names."""
    if not _ID.fullmatch(value) or ".." in value or value.endswith("."):
        raise ValueError("ID must be 1–128 ASCII letters, digits, dots, underscores or hyphens")
    if value.split(".", 1)[0].upper() in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        raise ValueError("ID is a reserved filename")
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class RubricItem(StrictModel):
    id: str
    step: str = Field(min_length=1, max_length=20_000)
    criteria: str = Field(min_length=1, max_length=20_000)
    partial: str | None = Field(default=None, max_length=20_000)
    points: float | None = Field(default=None, ge=0)

    _id_valid = field_validator("id")(validate_id)

    @field_validator("step", "criteria")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        return value


class Problem(StrictModel):
    id: str
    title: str = Field(min_length=1, max_length=1_000)
    prompt: str = Field(min_length=1, max_length=200_000)
    model_answer: str = Field(min_length=1, max_length=200_000)
    rubric: list[RubricItem] = Field(min_length=1, max_length=100)
    alternatives: str | None = Field(default=None, max_length=200_000)

    _id_valid = field_validator("id")(validate_id)

    @field_validator("title", "prompt", "model_answer")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        return value

    @model_validator(mode="after")
    def unique_items(self) -> Problem:
        identifiers = [item.id for item in self.rubric]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("rubric IDs must be unique")
        return self


class Answer(StrictModel):
    id: str
    problem: str
    source: Literal["synthetic", "learner"]
    error_types: list[str] = Field(default_factory=list, max_length=100)
    is_alternative: bool = False
    split: Literal["dev", "test"] | None = None
    body: str = Field(min_length=1, max_length=500_000)

    _ids_valid = field_validator("id", "problem")(validate_id)

    @field_validator("error_types")
    @classmethod
    def valid_error_types(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("error_types must not contain duplicates")
        for code in value:
            validate_id(code)
        return value

    @field_validator("body")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("answer body must not be blank")
        return value


class JudgementItem(StrictModel):
    rubric_id: str
    verdict: Verdict | None
    evidence: str = Field(max_length=100_000)
    reason: str = Field(min_length=1, max_length=100_000)
    feedback: str = Field(max_length=100_000)
    needs_review: bool = False

    _id_valid = field_validator("rubric_id")(validate_id)

    @model_validator(mode="after")
    def uncertainty_is_explicit(self) -> JudgementItem:
        if self.verdict is None and not self.needs_review:
            raise ValueError("an undecidable verdict must set needs_review=true")
        if not self.reason.strip():
            raise ValueError("a reason is required")
        if self.verdict in {"met", "partial"} and not self.evidence.strip():
            raise ValueError("met/partial judgments require a verbatim answer quotation")
        return self


class Result(StrictModel):
    answer_id: str
    problem_id: str
    mode: Mode
    items: list[JudgementItem] = Field(min_length=1, max_length=100)
    overall_feedback: str = Field(min_length=1, max_length=200_000)
    meta: dict[str, Any]

    _ids_valid = field_validator("answer_id", "problem_id")(validate_id)

    @model_validator(mode="after")
    def result_contract(self) -> Result:
        identifiers = [item.rubric_id for item in self.items]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("result rubric IDs must be unique")
        for key in ("model", "prompt_version", "created_at"):
            if not isinstance(self.meta.get(key), str) or not self.meta[key].strip():
                raise ValueError(f"meta.{key} is required")
        temperature = self.meta.get("temperature")
        if isinstance(temperature, bool) or not isinstance(temperature, (float, int)):
            raise ValueError("meta.temperature must be numeric")
        if not math.isfinite(temperature) or not 0 <= temperature <= 1:
            raise ValueError("meta.temperature must be in [0, 1]")
        try:
            stamp = datetime.fromisoformat(self.meta["created_at"].replace("Z", "+00:00"))
        except (ValueError, TypeError):
            raise ValueError("meta.created_at must be an ISO 8601 datetime") from None
        if stamp.utcoffset() is None:
            raise ValueError("meta.created_at must include a timezone")
        return self


def validate_result_context(result: Result, problem: Problem, answer: Answer) -> Result:
    """Validate joins and quotations in addition to the standalone JSON schema."""
    if answer.problem != problem.id:
        raise ValueError("answer.problem does not match problem.id")
    if result.answer_id != answer.id or result.problem_id != problem.id:
        raise ValueError("result IDs do not match the supplied inputs")
    expected = {item.id for item in problem.rubric}
    if {item.rubric_id for item in result.items} != expected:
        raise ValueError("result must contain exactly the problem's rubric IDs")
    for item in result.items:
        if item.evidence and item.evidence not in answer.body:
            raise ValueError(
                f"{item.rubric_id}: evidence must be a verbatim substring of the answer"
            )
    return result
