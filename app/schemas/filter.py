"""The generated listing filter file (`listing_filter_file.md`, R14, T9)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class FilterFile(BaseModel):
    """The structured output of the filter generation step, rendered to Markdown by code."""

    model_config = ConfigDict(extra="ignore")

    summary: str = Field(description="What the user is looking for, in a few sentences.")
    must_match: list[str] = Field(
        default_factory=list, description="Criteria a listing must meet to be relevant."
    )
    nice_to_match: list[str] = Field(
        default_factory=list, description="Criteria that make a listing more relevant."
    )
    exclusions: list[str] = Field(
        default_factory=list, description="What makes a listing irrelevant."
    )
    keywords_cs: list[str] = Field(default_factory=list, description="Search keywords in Czech.")
    keywords_en: list[str] = Field(default_factory=list, description="Search keywords in English.")


class FilterOutcome(BaseModel):
    """One generation call: the filter, or `error` (a short reason) on a model error."""

    model_config = ConfigDict(extra="forbid")

    filter: FilterFile | None = None
    truncated: bool = False
    error: str | None = None
