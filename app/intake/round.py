"""One conversational intake round: its state and the handlers of the intake agent's
five in-round tools (Phase 3 R6–R9, R18, T4, T5, T7).

`RoundContext` wraps the round's plain-JSON `RoundData` with what the handlers need
(the tool context, the reader model, the current directory). Nothing here writes
`profile.md` or `checklist.json`; sources are copied into the workspace as soon as
they are ingested (as in Phase 2), and their values are only collected.

- `record_item` is a direct answer (Phase 2 R13): `replace` sets the item's value,
  `append` adds a paragraph to the current one; it settles the conflicts raised on
  that item before it.
  The CV item is refused: it is satisfied by a source flagged `cv`.
- `mark_status` sets `partial` (with a hole note) or `known` on a filled item.
- `ingest_source` runs the allow rule (T7) before any file read or fetch; a URL
  allowed only through a bare `host/path` token is refused when its host is local
  (loopback, private, link-local, as for redirects); a `find_files` result needs a
  yes/no confirmation run here, default no, as does a path matched only by its file
  name. Then the source goes through the Phase 2
  sources module and the toolless reader; the agent gets a `SourceReport`, never the
  text. `cv=true` (also on a source already stored) flags it as the CV for the
  round; the manifest flag is written only by `write`.
- `find_files` searches by name (T11) in a folder only when the user typed it (or it
  is a default root); its results are kept for the allow rule.
- `finish` ends the round.

`merged()` replays the round's answers and source values in the order they happened,
so a source read after an answer adds to it (or conflicts with it), and an answer
replaces what a source wrote before it. `status()` is the checklist status as it
would be saved if the round ended now; the
context message (`context_payload`) shows it to the agent with each value cut at
`VALUE_LIMIT` characters (T5).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from app.intake.checklist import CV_ITEM, ITEMS
from app.intake.detect import allow_folder, allow_ingest
from app.intake.extract import CHAR_BUDGET, extract, to_updates
from app.intake.find import default_roots, find_files
from app.intake.profile import Answer, is_filled, item_body, load_profile, merge, merge_steps
from app.intake.sources import (
    ingest_source,
    is_local_host,
    load_manifest,
    source_text,
)
from app.intake.status import (
    apply_changes,
    checklist_path,
    load_status,
    now_utc,
    reconcile,
    source_origin,
)
from app.messages import escape_controls, say
from app.schemas.checklist import ORIGIN_CONVERSATION, ChecklistStatus, StatusChange
from app.schemas.intake import NoteKind, RoundNote
from app.schemas.intake_agent import (
    FindFilesArgs,
    FindFilesResult,
    FinishArgs,
    FinishResult,
    IngestSourceArgs,
    IngestSourceResult,
    MarkStatusArgs,
    MarkStatusResult,
    RecordItemArgs,
    RecordItemResult,
    RoundData,
    RoundEvent,
    SourceReport,
)
from app.schemas.profile import ItemId, ItemUpdate, MergeResult, ProfileDoc
from app.schemas.sources import Manifest, ManifestEntry, SourceResult

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel
    from pydantic import BaseModel

    from app.guard import Scorer
    from app.tools.context import ToolContext

VALUE_LIMIT = 1500
# the in-round tools (T4): name → argument model, in the order they are bound; the
# handler of each is the `RoundContext` method of the same name
TOOLS: dict[str, type[BaseModel]] = {
    "record_item": RecordItemArgs,
    "mark_status": MarkStatusArgs,
    "ingest_source": IngestSourceArgs,
    "find_files": FindFilesArgs,
    "finish": FinishArgs,
}
TOOL_NAMES = tuple(TOOLS)
CUT_MARK = " […]"


def cut_value(text: str, limit: int = VALUE_LIMIT) -> str:
    """`text` within `limit` characters, the cut marked (T5)."""
    return text if len(text) <= limit else text[:limit] + CUT_MARK


@dataclass
class RoundContext:
    """The round's state and services. `reader` is the model of the toolless source
    reader (by default the agent's own chat model, unbound)."""

    ctx: ToolContext
    reader: BaseChatModel
    data: RoundData = field(default_factory=RoundData)
    cwd: Path | None = None
    scorer: Scorer | None = None
    home: Path | None = None
    # reports of the sources `ingest` handled through this context (not part of the
    # round state): the graph gives them back to the agent after a failed turn
    ingested: list[SourceReport] = field(default_factory=list)
    # the stored `checklist.json` as `open` read it (`None` when missing or invalid)
    stored: ChecklistStatus | None = None

    @classmethod
    def open(
        cls,
        ctx: ToolContext,
        reader: BaseChatModel,
        *,
        cwd: Path | None = None,
        scorer: Scorer | None = None,
        home: Path | None = None,
    ) -> RoundContext:
        """A new round over `profile.md` and the reconciled status as they are now; an
        invalid `checklist.json` is noted (R4)."""
        base = load_profile(ctx.workspace)
        loaded = load_status(ctx.workspace)
        started = reconcile(base, load_manifest(ctx.workspace), loaded.stored, now=now_utc())
        round_ = cls(ctx, reader, RoundData(base=base, started=started), cwd, scorer, home)
        round_.stored = loaded.stored
        if loaded.invalid:
            path = str(checklist_path(ctx.workspace))
            round_.note("status_rebuilt", path, ctx.msg("intake.note.status_rebuilt", path=path))
        return round_

    # --- views -----------------------------------------------------------------------

    def merged(self) -> tuple[ProfileDoc, MergeResult]:
        """The round's one merge of the direct answers and source values into the base
        profile, replayed in event order: what `write` saves and what the status and
        context are computed from. `MergeResult.applied` holds `RoundData.updates`
        indexes."""
        steps: list[ItemUpdate | Answer] = []
        order: list[int] = []  # the `updates` index of each update step
        for event in self.data.events:
            if event.kind == "update" and event.index is not None:
                steps.append(self.data.updates[event.index])
                order.append(event.index)
            elif event.kind == "answer":
                text = event.text
                if text is None:
                    text = self.data.answers.get(event.item_id, "")
                steps.append(Answer(event.item_id, text))
        doc, result = merge_steps(self.data.base, steps, texts=self.ctx.texts)
        result.applied = [order[i] for i in result.applied]
        return doc, result

    def doc(self) -> ProfileDoc:
        """The profile as it would be if the round ended now."""
        return self.merged()[0]

    def manifest(self) -> Manifest:
        """The manifest with the sources this round called a CV flagged."""
        manifest = load_manifest(self.ctx.workspace)
        for entry in manifest.sources:
            if entry.slug in self.data.cv_slugs:
                entry.cv = True
        return manifest

    def status(self, now: datetime | None = None) -> ChecklistStatus:
        """The status as it would be saved now: what the round learned, in order, applied
        to the status at the start, then reconciled with the profile (R4, R7, R9).

        A source value counts only when the merge wrote it and no later `replace`
        answer on its item dropped it (an `append` answer keeps its text, so its
        origin stays); a direct answer makes its item `known` from `conversation`;
        `mark_status` sets state and note and keeps the origins."""
        now = now or now_utc()
        doc, merged = self.merged()
        applied = set(merged.applied)
        replaced: dict[str, int] = {}  # item id → position of its last `replace` answer
        for position, event in enumerate(self.data.events):
            if event.kind == "answer" and event.mode != "append":
                replaced[event.item_id] = position
        status = self.data.started.model_copy(deep=True)
        for position, event in enumerate(self.data.events):
            change: StatusChange | None = None
            if event.kind == "mark":
                change = StatusChange(
                    item_id=event.item_id, state=event.state or "known", note=event.note
                )
            elif event.kind == "update" and event.index is not None:
                update = self.data.updates[event.index]
                if event.index in applied and replaced.get(update.item_id, -1) < position:
                    change = StatusChange(
                        item_id=update.item_id,
                        state="partial" if update.gap else "known",
                        note=update.gap,
                        origin=update.source or ORIGIN_CONVERSATION,
                    )
            elif event.kind == "answer":
                change = StatusChange(
                    item_id=event.item_id, state="known", origin=ORIGIN_CONVERSATION
                )
            elif event.kind == "cv" and event.origin:
                change = StatusChange(item_id=CV_ITEM, state="known", origin=event.origin)
            if change is not None:
                status = apply_changes(status, [change], now=now)
        return reconcile(doc, self.manifest(), status, now=now)

    def context_payload(self) -> str:
        """The context message before a user message (T5): the checklist status with
        each current value cut at `VALUE_LIMIT`, and the source reports since the last
        turn, as JSON. Taking it clears the pending reports."""
        status = self.status()
        doc = self.doc()
        manifest = self.manifest()
        items = []
        for item in ITEMS:
            entry = status.items.get(item.id)
            items.append(
                {
                    "id": item.id,
                    "title": item.title(self.ctx.texts),
                    "group": item.group,
                    "state": entry.state if entry else "missing",
                    "note": entry.note if entry else None,
                    "origins": list(entry.origins) if entry else [],
                    "value": cut_value(item_body(doc, item.id, manifest).strip()),
                }
            )
        reports = [r.model_dump(mode="json") for r in self.data.reports]
        self.data.reports = []
        payload = {"checklist_status": items, "source_reports": reports}
        return json.dumps(payload, ensure_ascii=False, indent=1)

    def note(self, kind: NoteKind, subject: str, text: str) -> None:
        self.data.notes.append(RoundNote(kind=kind, subject=subject, text=text))

    # --- sources (R5, R7) ------------------------------------------------------------

    def read(self, entry: ManifestEntry, report: SourceReport | None = None) -> None:
        """Read one stored source with the toolless reader; its values become fill-only
        updates. `report` is filled in with what they did to the profile."""
        ctx = self.ctx
        try:
            text = source_text(ctx.workspace, entry.slug)
        except (OSError, UnicodeDecodeError):
            self.note(
                "source_text_missing",
                entry.origin,
                ctx.msg("intake.note.source_text_missing", origin=entry.origin),
            )
            return
        before = self.doc()
        outcome = extract(self.reader, ctx.texts, before, text=text, source=True)
        if outcome.truncated:
            self.note(
                "truncated",
                entry.origin,
                ctx.msg("intake.note.truncated", origin=entry.origin, limit=CHAR_BUDGET),
            )
        if outcome.extraction is None:
            self.note(
                "model_error_source",
                entry.origin,
                ctx.msg(
                    "intake.note.model_error_source", origin=entry.origin, detail=outcome.error
                ),
            )
            return
        new = to_updates(outcome.extraction, entry.origin, source=source_origin(entry))
        _after, merged = merge(before, updates=new)
        for update in new:
            self.data.events.append(
                RoundEvent(kind="update", item_id=update.item_id, index=len(self.data.updates))
            )
            self.data.updates.append(update)
        self.data.extracted.append(entry.slug)
        if outcome.extraction.is_cv:
            self._flag_cv(entry)
        if report is not None:
            report.read = True
            report.filled = [i for i in merged.changed if not before.body(i).strip()]
            report.added_to = [i for i in merged.changed if before.body(i).strip()]
            report.conflicts = list(dict.fromkeys(c.item_id for c in merged.conflicts))
            report.cv = report.cv or entry.slug in self.data.cv_slugs

    def _flag_cv(self, entry: ManifestEntry) -> None:
        if entry.slug not in self.data.cv_slugs:
            self.data.cv_slugs.append(entry.slug)
            self.data.events.append(
                RoundEvent(kind="cv", item_id=CV_ITEM, origin=source_origin(entry))
            )

    def ingest(self, spec: str, *, cv: bool = False) -> tuple[SourceResult, SourceReport]:
        """Ingest one source through the Phase 2 sources module (guard, store, manifest,
        dedupe), read it when added, and report it (R5, R7). `cv` flags it as the CV,
        also when it is already stored (R6); the flag goes into `cv_slugs`, and only
        `write` sets it in the manifest, so a cancelled round leaves it unset (R10)."""
        result = ingest_source(self.ctx, spec, cwd=self.cwd, scorer=self.scorer)
        self.data.sources.append(result)
        report = SourceReport(origin=result.origin, status=result.status, error=result.code)
        entry = load_manifest(self.ctx.workspace).by_slug(result.slug) if result.slug else None
        if entry is not None:
            if cv and result.status in ("added", "skipped_duplicate"):
                self._flag_cv(entry)
                report.cv = True
            if result.status == "added":
                self.read(entry, report)
        self._add_states(report)
        self.ingested.append(report)
        return result, report

    def read_stored(self, entry: ManifestEntry) -> SourceReport:
        """Read a source already stored but not yet extracted (a drop-in, or one an
        earlier round left unextracted) and report it as `added` (R7)."""
        report = SourceReport(origin=entry.origin, status="added")
        self.read(entry, report)
        self._add_states(report)
        return report

    def _add_states(self, report: SourceReport) -> None:
        touched = dict.fromkeys([*report.filled, *report.added_to, *report.conflicts])
        if report.cv:
            touched[CV_ITEM] = None
        if touched:
            status = self.status()
            report.states = {i: status.items[i].state for i in touched if i in status.items}

    def announce(self, result: SourceResult, report: SourceReport) -> None:
        """Print the one-line chat report of a source (R5)."""
        say(self.ctx.console, self.report_line(result, report), _style(result))

    def report_line(self, result: SourceResult, report: SourceReport) -> str:
        """The one-line chat report of a source (R5), in the UI language."""
        msg = self.ctx.msg
        name = _display_name(result)
        if result.status != "added":
            return msg(
                f"intake.report.{result.status}",
                origin=name,
                id=result.quarantine_id or "",
                reason=result.reason or "",
            )
        parts = [msg("intake.report.added", origin=name)]
        for key, ids in (
            ("filled", report.filled),
            ("added_to", report.added_to),
            ("conflicts", report.conflicts),
        ):
            if ids:
                parts.append(msg(f"intake.report.{key}", items=self._titles(ids)))
        if report.cv:
            parts.append(msg("intake.report.cv"))
        return "; ".join(parts)

    def _titles(self, ids: list[ItemId]) -> str:
        titles = {i.id: i.title(self.ctx.texts) for i in ITEMS}
        return ", ".join(titles[i] for i in ids)

    # --- tool handlers (T4) ------------------------------------------------------------

    def record_item(self, args: RecordItemArgs) -> RecordItemResult:
        if args.item_id == CV_ITEM:
            return RecordItemResult(ok=False, item_id=args.item_id, refused="cv_needs_source")
        text = args.text.strip()
        if not text:
            return RecordItemResult(ok=False, item_id=args.item_id, refused="empty_text")
        if args.mode == "append":
            current = self.doc().body(args.item_id).strip()
            text = f"{current}\n\n{text}" if current else text
        self.data.answers[args.item_id] = text
        self.data.events.append(
            RoundEvent(kind="answer", item_id=args.item_id, text=text, mode=args.mode)
        )
        return RecordItemResult(ok=True, item_id=args.item_id)

    def mark_status(self, args: MarkStatusArgs) -> MarkStatusResult:
        if not is_filled(self.doc(), args.item_id, self.manifest()):
            return MarkStatusResult(ok=False, item_id=args.item_id, refused="item_empty")
        note = ((args.note or "").strip() or None) if args.state == "partial" else None
        self.data.events.append(
            RoundEvent(kind="mark", item_id=args.item_id, state=args.state, note=note)
        )
        return MarkStatusResult(ok=True, item_id=args.item_id, state=args.state)

    def ingest_source(self, args: IngestSourceArgs) -> IngestSourceResult:
        # the agent saw `find_files` paths with controls escaped; map them back
        shown = {escape_controls(p): p for p in self.data.found}
        spec = shown.get(args.spec.strip(), args.spec)
        verdict = allow_ingest(
            spec,
            self.data.user_messages,
            self.data.found,
            home=self.home,
            cwd=self.cwd or Path.cwd(),
            detected=self.data.detected,
        )
        if verdict.decision == "refused":
            return IngestSourceResult(ok=False, spec=args.spec, refused=verdict.reason)
        if verdict.kind == "url" and verdict.bare_host:
            try:
                host = urlsplit(verdict.spec).hostname
            except ValueError:
                host = None
            if not host:
                return IngestSourceResult(ok=False, spec=args.spec, refused="invalid_url")
            if is_local_host(host, self.ctx.resolve_host):
                return IngestSourceResult(ok=False, spec=args.spec, refused="local_host")
        if verdict.decision == "confirm":
            question = self.ctx.msg("intake.confirm_found", path=escape_controls(verdict.spec))
            if not self.ctx.confirm(question, default=False):
                return IngestSourceResult(ok=False, spec=args.spec, refused="declined")
        result, report = self.ingest(verdict.spec, cv=args.cv)
        self.announce(result, report)
        return IngestSourceResult(ok=True, spec=verdict.spec, report=report)

    def find_files(self, args: FindFilesArgs) -> FindFilesResult:
        cwd = self.cwd or Path.cwd()
        if args.folder is not None:
            home = self.home if self.home is not None else Path.home()
            if not allow_folder(
                args.folder,
                self.data.user_messages,
                home=home,
                cwd=cwd,
                detected=self.data.detected,
                roots=default_roots(cwd, home),
            ):
                return FindFilesResult(error="folder_not_typed")
        found = find_files(
            args.name, args.folder, cwd=cwd, workspace=self.ctx.workspace, home=self.home
        )
        for path in found.paths:
            if path not in self.data.found:
                self.data.found.append(path)
        return FindFilesResult(
            paths=[escape_controls(p) for p in found.paths],
            error=found.error,
            truncated=found.truncated,
        )

    def finish(self, args: FinishArgs) -> FinishResult:
        self.data.finished = True
        return FinishResult()

    def handlers(self) -> dict[str, tuple[type[BaseModel], Callable[[Any], BaseModel]]]:
        """Tool name → (argument model, handler), in the order the tools are bound; each
        handler is the method named like its tool."""
        return {name: (schema, getattr(self, name)) for name, schema in TOOLS.items()}


def _display_name(result: SourceResult) -> str:
    if result.type == "url":
        return result.origin
    return Path(result.origin).name or result.origin


def _style(result: SourceResult) -> str | None:
    return {"added": None, "skipped_duplicate": "dim"}.get(result.status, "yellow")


__all__ = ["CUT_MARK", "TOOLS", "TOOL_NAMES", "VALUE_LIMIT", "RoundContext", "cut_value"]
