"""Profile sources: the manifest record per stored source and the per-source outcome (R10, R11)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SourceType = Literal["txt", "md", "pdf", "html", "url"]
SourceStatus = Literal[
    "added", "skipped_duplicate", "quarantined", "refused_no_classifier", "error"
]
# Why a source ended as `error`; the localized text is in `SourceResult.reason`.
SourceErrorCode = Literal[
    "not_found",
    "unsupported",
    "unreadable",
    "empty",
    "fetch_status",
    "fetch_timeout",
    "fetch_type",
    "fetch_too_large",
    "fetch_redirects",
    "fetch_failed",
    "quarantine_failed",
]

SLUG_PATTERN = r"^[a-z0-9][a-z0-9-]*$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"


class ManifestEntry(BaseModel):
    """One stored source in `profile/sources/manifest.json`.

    `origin` is what the user gave (a path or URL) or the dropped-in file's path;
    `extracted` turns true only after a round that extracted it wrote `profile.md`.
    """

    model_config = ConfigDict(extra="forbid")

    slug: str = Field(pattern=SLUG_PATTERN)
    origin: str = Field(min_length=1)
    sha256: str = Field(pattern=SHA256_PATTERN)
    type: SourceType
    cv: bool = False
    extracted: bool = False
    added_at: datetime


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    sources: list[ManifestEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique(self) -> Manifest:
        slugs = [e.slug for e in self.sources]
        if len(slugs) != len(set(slugs)):
            raise ValueError("source slugs must be unique")
        return self

    def by_sha(self, sha256: str) -> ManifestEntry | None:
        return next((e for e in self.sources if e.sha256 == sha256), None)

    def by_slug(self, slug: str) -> ManifestEntry | None:
        return next((e for e in self.sources if e.slug == slug), None)


class SourceResult(BaseModel):
    """What happened to one supplied or dropped-in source.

    `slug` is the stored folder (`added`) or the existing one (`skipped_duplicate`);
    `quarantine_id` is set exactly for `quarantined`; `reason` is a user-facing text
    (the guard's reason, or why the source failed) and `code` the error kind.
    """

    model_config = ConfigDict(extra="forbid")

    origin: str
    status: SourceStatus
    type: SourceType | None = None
    slug: str | None = None
    final_url: str | None = None
    dropped_in: bool = False
    quarantine_id: str | None = None
    code: SourceErrorCode | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> SourceResult:
        if (self.status == "quarantined") != (self.quarantine_id is not None):
            raise ValueError("quarantine_id is set exactly for quarantined sources")
        if (self.status == "error") != (self.code is not None):
            raise ValueError("code is set exactly for errors")
        if self.status in ("added", "skipped_duplicate") and not self.slug:
            raise ValueError("added and skipped sources name their slug")
        if self.status in ("quarantined", "refused_no_classifier", "error") and not self.reason:
            raise ValueError("a refused or failed source needs a reason")
        return self
