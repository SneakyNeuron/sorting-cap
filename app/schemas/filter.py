"""The generated listing filter file (`listing_filter_file.md`, R14, T9)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class FilterFile(BaseModel):
    """The structured output of the filter generation step, rendered to Markdown by code."""

    model_config = ConfigDict(extra="ignore")

    summary: str = Field(
        description=(
            "What the job seeker is looking for, in two to four sentences of prose, "
            "using only facts from the profile and the sources."
        )
    )
    must_match: list[str] = Field(
        default_factory=list,
        description=(
            "Short one-line criteria a listing must meet to be relevant (role, key "
            "skills, required location, work mode or contract type). Empty when nothing "
            "supports one."
        ),
    )
    nice_to_match: list[str] = Field(
        default_factory=list,
        description=(
            "Short one-line criteria that make a listing more relevant but are not "
            "required. Empty when nothing supports one."
        ),
    )
    exclusions: list[str] = Field(
        default_factory=list,
        description=(
            "Short one-line criteria that make a listing irrelevant: roles, industries, "
            "companies or conditions the person avoids or that clearly do not fit."
        ),
    )
    keywords_cs: list[str] = Field(
        default_factory=list,
        description="Search keywords and short phrases in Czech for job boards.",
    )
    keywords_en: list[str] = Field(
        default_factory=list,
        description="Search keywords and short phrases in English for job boards.",
    )


class FilterOutcome(BaseModel):
    """One generation call: the filter (and the hash of the profile it was made from), or
    `error` (a short reason) on a model error."""

    model_config = ConfigDict(extra="forbid")

    filter: FilterFile | None = None
    truncated: bool = False
    error: str | None = None
    # SHA-256 of the `profile.md` bytes the filter was generated from
    profile_sha256: str | None = None
