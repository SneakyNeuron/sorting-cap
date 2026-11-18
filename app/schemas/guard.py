"""Injection guard results: the verdict on one input and the quarantine report (R2, R5)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Probability = Field(default=None, ge=0.0, le=1.0)


class Verdict(BaseModel):
    """What the guard decided about one input.

    `matched_rules` holds rule names from `app.guard.rules` (hidden-text hits carry a
    `hidden:` prefix); `max_score` is the highest classifier `INJECTION` probability
    over all windows, `None` when the classifier did not run; `reason` is a short
    human-readable cause, required when flagged.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["clean", "flagged"]
    matched_rules: list[str] = Field(default_factory=list)
    max_score: float | None = Probability
    reason: str | None = None

    @property
    def flagged(self) -> bool:
        return self.status == "flagged"

    @model_validator(mode="after")
    def _consistent(self) -> Verdict:
        if self.status == "flagged" and not (self.reason and self.reason.strip()):
            raise ValueError("a flagged verdict needs a reason")
        if self.status == "clean" and self.matched_rules:
            raise ValueError("a clean verdict cannot have matched rules")
        return self


class QuarantineReport(BaseModel):
    """`quarantine/<id>/report.json`: metadata only, never an excerpt of the content."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    created_at: datetime
    matched_rules: list[str] = Field(default_factory=list)
    max_score: float | None = Probability
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["quarantined", "released"] = "quarantined"
    released_at: datetime | None = None

    @model_validator(mode="after")
    def _consistent(self) -> QuarantineReport:
        if not self.matched_rules and self.max_score is None:
            raise ValueError("a quarantine report needs matched rules or a score")
        if (self.status == "released") != (self.released_at is not None):
            raise ValueError("released_at is set exactly when the item is released")
        return self
