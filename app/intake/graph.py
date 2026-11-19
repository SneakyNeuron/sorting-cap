"""The intake round as a LangGraph `StateGraph` (R9, R10, R13, T8, T10).

    start → ask_sources* → ingest → intro → ask* → write → filter
    start → ingest → write → filter                      (headless)

`start` reads `profile.md` and picks up dropped-in files (R11). `ask_sources` collects
paths and URLs one per line (empty line to continue); `ingest` copies them and extracts
every source not yet extracted (R10) from its stored `text.md`. `intro` takes the
free-form introduction, `ask` one prewritten question per still-missing item in R12
order. Questions are asked with `interrupt()`; `run_round` resumes the graph with
`ctx.text()` answers under one thread id per round. `next` (or an empty answer) skips a
question, `done` ends the round at once (localized words, R9).

Nothing is written to `profile.md` before `write`: a cancelled round (Ctrl-C / Ctrl-D)
leaves it as it was, and sources copied so far stay with `extracted` false, so the next
round extracts them. `write` merges (answers replace, sources and the introduction fill,
R13), saves `profile.md`, then `profile/checklist.json` whenever the reconciled
status differs from the stored file (Phase 3 R12, `app/intake/status.py`), and only
then marks the extracted sources. `filter`
regenerates `listing_filter_file.md` when the profile changed or the file is missing
(R14, T9); on a model error, or a Ctrl-C during that call, the previous file is kept
and the summary notes it.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from app.intake.checklist import CV_ITEM, ITEMS, item
from app.intake.extract import CHAR_BUDGET, extract, to_updates
from app.intake.filter_file import generate, needs_filter, stored_texts, write_filter
from app.intake.profile import (
    cv_body,
    load_profile,
    merge,
    missing_items,
    profile_path,
    save_profile,
)
from app.intake.sources import (
    ingest_source,
    ingest_sources,
    load_manifest,
    mark_extracted,
    scan_dropped,
    set_cv,
    source_text,
    unextracted,
)
from app.intake.status import (
    apply_changes,
    checklist_path,
    holes,
    load_status,
    now_utc,
    reconcile,
    round_changes,
    save_status,
    source_origin,
)
from app.schemas.checklist import ChecklistStatus
from app.schemas.intake import NoteKind, RoundNote, RoundResult, RoundState
from app.schemas.profile import ItemUpdate, ProfileDoc
from app.schemas.sources import Manifest, ManifestEntry, SourceResult

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

    from app.guard import Scorer
    from app.tools.context import ToolContext

RECURSION_LIMIT = 10_000
Answer = Literal["done", "skip", "text"]


def words(ctx: ToolContext, key: str) -> set[str]:
    """A comma-separated word list from the catalog, case-folded."""
    return {w.strip().casefold() for w in ctx.msg(key).split(",") if w.strip()}


def classify(ctx: ToolContext, answer: str) -> Answer:
    """`done` ends the round, `next` or an empty answer skips, anything else is text."""
    word = answer.strip().casefold()
    if not word or word in words(ctx, "intake.words.next"):
        return "skip"
    if word in words(ctx, "intake.words.done"):
        return "done"
    return "text"


class _Round:
    """Read and change one node's view of the state; `changes()` is the node's return."""

    def __init__(self, state: RoundState) -> None:
        self.state = state
        self.sources = list(state.get("sources", []))
        self.updates = list(state.get("updates", []))
        self.answers = dict(state.get("answers", {}))
        self.extracted = list(state.get("extracted", []))
        self.cv_slugs = list(state.get("cv_slugs", []))
        self.notes = list(state.get("notes", []))

    def changes(self, **extra: Any) -> dict[str, Any]:
        return {
            "sources": self.sources,
            "updates": self.updates,
            "answers": self.answers,
            "extracted": self.extracted,
            "cv_slugs": self.cv_slugs,
            "notes": self.notes,
            **extra,
        }

    def base(self) -> ProfileDoc:
        return ProfileDoc.model_validate(self.state.get("base", {}))

    def item_updates(self) -> list[ItemUpdate]:
        return [ItemUpdate.model_validate(u) for u in self.updates]

    def doc(self) -> ProfileDoc:
        """The profile as it would be if the round ended now."""
        return merge(self.base(), answers=self.answers, updates=self.item_updates())[0]

    def manifest(self, workspace: Path) -> Manifest:
        """The manifest with the sources the extraction called a CV flagged."""
        manifest = load_manifest(workspace)
        for entry in manifest.sources:
            if entry.slug in self.cv_slugs:
                entry.cv = True
        return manifest

    def note(self, kind: NoteKind, subject: str, text: str) -> None:
        self.notes.append(RoundNote(kind=kind, subject=subject, text=text).model_dump())

    def add_sources(self, results: Iterable[SourceResult]) -> None:
        self.sources.extend(r.model_dump(mode="json") for r in results)

    def add_updates(self, updates: Iterable[ItemUpdate]) -> None:
        self.updates.extend(u.model_dump(mode="json") for u in updates)


def build_graph(
    ctx: ToolContext,
    model: BaseChatModel,
    *,
    cwd: Path | None = None,
    scorer: Scorer | None = None,
):
    """The compiled intake graph with an in-memory checkpointer (T10)."""
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import interrupt

    texts = ctx.texts

    def truncated(r: _Round, subject: str) -> None:
        r.note(
            "truncated",
            subject,
            ctx.msg("intake.note.truncated", origin=subject, limit=CHAR_BUDGET),
        )

    def extract_source(r: _Round, entry: ManifestEntry) -> None:
        try:
            text = source_text(ctx.workspace, entry.slug)
        except (OSError, UnicodeDecodeError):
            r.note(
                "source_text_missing",
                entry.origin,
                ctx.msg("intake.note.source_text_missing", origin=entry.origin),
            )
            return
        outcome = extract(model, texts, r.doc(), text=text, source=True)
        if outcome.truncated:
            truncated(r, entry.origin)
        if outcome.extraction is None:
            r.note(
                "model_error_source",
                entry.origin,
                ctx.msg(
                    "intake.note.model_error_source", origin=entry.origin, detail=outcome.error
                ),
            )
            return
        r.add_updates(to_updates(outcome.extraction, entry.origin, source=source_origin(entry)))
        r.extracted.append(entry.slug)
        if outcome.extraction.is_cv and entry.slug not in r.cv_slugs:
            r.cv_slugs.append(entry.slug)

    # --- nodes -------------------------------------------------------------------

    def start(state: RoundState) -> dict[str, Any]:
        r = _Round(state)
        r.add_sources(scan_dropped(ctx, scorer=scorer))
        base = load_profile(ctx.workspace)
        loaded = load_status(ctx.workspace)
        if loaded.invalid:
            path = str(checklist_path(ctx.workspace))
            r.note("status_rebuilt", path, ctx.msg("intake.note.status_rebuilt", path=path))
        status = reconcile(base, load_manifest(ctx.workspace), loaded.stored, now=now_utc())
        return r.changes(
            base=base.model_dump(mode="json"),
            status=status.model_dump(mode="json"),
            status_stored=loaded.stored.model_dump(mode="json") if loaded.stored else None,
            profile_existed=profile_path(ctx.workspace).exists(),
            specs=list(state.get("specs", [])),
            asked=[],
            current=None,
            done=False,
            collecting=not state.get("headless", False),
        )

    def ask_sources(state: RoundState) -> dict[str, Any]:
        specs = list(state.get("specs", []))
        key = "intake.ask.source_more" if specs else "intake.ask.sources"
        answer = interrupt({"kind": "source", "prompt": ctx.msg(key)})
        kind = classify(ctx, answer)
        if kind == "done":
            return {"collecting": False, "done": True}
        if kind == "skip":
            return {"collecting": False}
        return {"specs": [*specs, answer.strip()]}

    def ingest(state: RoundState) -> dict[str, Any]:
        r = _Round(state)
        r.add_sources(ingest_sources(ctx, state.get("specs", []), cwd=cwd, scorer=scorer))
        for entry in unextracted(ctx.workspace):
            extract_source(r, entry)
        return r.changes()

    def intro(state: RoundState) -> dict[str, Any]:
        answer = interrupt({"kind": "intro", "prompt": ctx.msg("intake.ask.intro")})
        kind = classify(ctx, answer)
        if kind == "done":
            return {"done": True}
        if kind == "skip":
            return {}
        r = _Round(state)
        origin = ctx.msg("intake.origin.intro")
        outcome = extract(model, texts, r.doc(), text=answer)
        if outcome.truncated:
            truncated(r, origin)
        if outcome.extraction is None:
            r.note(
                "model_error_intro",
                origin,
                ctx.msg("intake.note.model_error_intro", detail=outcome.error),
            )
        else:
            r.add_updates(to_updates(outcome.extraction, origin))
        return r.changes()

    def next_item(r: _Round) -> str | None:
        asked = set(r.state.get("asked", []))
        missing = set(missing_items(r.doc(), r.manifest(ctx.workspace)))
        return next((i.id for i in ITEMS if i.id in missing and i.id not in asked), None)

    def ask(state: RoundState) -> dict[str, Any]:
        r = _Round(state)
        item_id = next_item(r)
        if item_id is None:
            return {"current": None}
        asked = list(state.get("asked", []))
        request = {
            "kind": "question",
            "item_id": item_id,
            "prompt": item(item_id).question(texts),
            "note": None if asked else ctx.msg("intake.ask.hint"),
        }
        answer = interrupt(request)
        changes = {"asked": [*asked, item_id], "current": item_id}
        kind = classify(ctx, answer)
        if kind == "done":
            return {**changes, "done": True}
        if kind == "skip":
            return changes
        if item_id == CV_ITEM:
            result = ingest_source(ctx, answer, cwd=cwd, cv=True, scorer=scorer)
            r.add_sources([result])
            if result.status == "added" and result.slug:
                entry = load_manifest(ctx.workspace).by_slug(result.slug)
                if entry is not None:
                    extract_source(r, entry)
            return r.changes(**changes)
        title = item(item_id).title(texts)
        outcome = extract(model, texts, r.doc(), text=answer, question=item_id)
        if outcome.truncated:
            truncated(r, title)
        value = getattr(outcome.extraction, item_id, None) if outcome.extraction else None
        if outcome.extraction is None:
            r.note(
                "model_error_answer",
                title,
                ctx.msg("intake.note.model_error_answer", title=title, detail=outcome.error),
            )
        else:
            origin = ctx.msg("intake.origin.answer", title=title)
            r.add_updates(to_updates(outcome.extraction, origin, skip=item_id))
        # the answer replaces the item: the extracted value, or the text as typed
        r.answers[item_id] = value.text.strip() if value and value.text.strip() else answer.strip()
        return r.changes(**changes)

    def write(state: RoundState) -> dict[str, Any]:
        r = _Round(state)
        base = r.base()
        updates = r.item_updates()
        new_doc, merged = merge(base, answers=r.answers, updates=updates, texts=texts)
        for slug in r.cv_slugs:
            set_cv(ctx.workspace, slug)
        manifest = load_manifest(ctx.workspace)
        round_slugs = {s.get("slug") for s in r.sources if s.get("status") == "added"}
        cv_entries = [
            e for e in manifest.sources if e.cv and (e.slug in r.cv_slugs or e.slug in round_slugs)
        ]
        now = now_utc()
        started = ChecklistStatus.model_validate(state.get("status", {}))
        learned = round_changes(updates, merged.applied, r.answers, cv_entries)
        status = reconcile(new_doc, manifest, apply_changes(started, learned, now=now), now=now)
        status_written = status.model_dump(mode="json") != state.get("status_stored")
        cv_changed = cv_body(manifest).strip() != base.body(CV_ITEM).strip()
        changed = list(merged.changed)
        if cv_changed:
            changed = [i.id for i in ITEMS if i.id in changed or i.id == CV_ITEM]
        written = bool(changed or r.extracted or not state.get("profile_existed", False))
        if written:
            save_profile(ctx.workspace, new_doc, texts, manifest)
        if status_written:  # after `profile.md`, also when only the status changed (R12)
            save_status(ctx.workspace, status)
        if written:
            mark_extracted(ctx.workspace, r.extracted)
        result = RoundResult(
            sources=[SourceResult.model_validate(s) for s in r.sources],
            changed=changed,
            missing=missing_items(new_doc, manifest),
            partial=holes(status),
            conflicts=merged.conflicts,
            notes=[RoundNote.model_validate(n) for n in r.notes],
            extracted=r.extracted if written else [],
            profile_written=written,
            status_written=status_written,
            profile_changed=bool(changed),
        )
        return {"result": result.model_dump(mode="json")}

    def filter_node(state: RoundState) -> dict[str, Any]:
        result = RoundResult.model_validate(state["result"])
        if not needs_filter(ctx.workspace, profile_changed=result.profile_changed):
            return {}
        sources, unreadable = stored_texts(ctx.workspace, load_manifest(ctx.workspace))
        for origin in unreadable:
            result.notes.append(
                RoundNote(
                    kind="filter_text_missing",
                    subject=origin,
                    text=ctx.msg("intake.note.filter_text_missing", origin=origin),
                )
            )
        outcome = generate(model, texts, ctx.workspace, sources=sources)
        if outcome.truncated:
            origin = ctx.msg("intake.origin.filter_sources")
            result.notes.append(
                RoundNote(
                    kind="truncated",
                    subject=origin,
                    text=ctx.msg("intake.note.truncated", origin=origin, limit=CHAR_BUDGET),
                )
            )
        if outcome.filter is None:
            result.notes.append(
                RoundNote(
                    kind="model_error_filter",
                    subject=ctx.msg("intake.origin.filter_sources"),
                    text=ctx.msg("intake.note.model_error_filter", detail=outcome.error),
                )
            )
        else:
            write_filter(
                ctx.workspace,
                outcome.filter,
                texts,
                profile_sha256=outcome.profile_sha256 or "",
            )
            result.filter_written = True
        return {"result": result.model_dump(mode="json")}

    # --- edges -------------------------------------------------------------------

    def after_start(state: RoundState) -> str:
        return "ask_sources" if state.get("collecting") else "ingest"

    def after_sources(state: RoundState) -> str:
        return "ask_sources" if state.get("collecting") else "ingest"

    def after_ingest(state: RoundState) -> str:
        return "write" if state.get("headless") or state.get("done") else "intro"

    def after_intro(state: RoundState) -> str:
        return "write" if state.get("done") else "ask"

    def after_ask(state: RoundState) -> str:
        return "write" if state.get("done") or state.get("current") is None else "ask"

    graph = StateGraph(RoundState)
    for name, node in (
        ("start", start),
        ("ask_sources", ask_sources),
        ("ingest", ingest),
        ("intro", intro),
        ("ask", ask),
        ("write", write),
        ("filter", filter_node),
    ):
        graph.add_node(name, node)
    graph.add_edge(START, "start")
    graph.add_conditional_edges("start", after_start, ["ask_sources", "ingest"])
    graph.add_conditional_edges("ask_sources", after_sources, ["ask_sources", "ingest"])
    graph.add_conditional_edges("ingest", after_ingest, ["intro", "write"])
    graph.add_conditional_edges("intro", after_intro, ["ask", "write"])
    graph.add_conditional_edges("ask", after_ask, ["ask", "write"])
    graph.add_edge("write", "filter")
    graph.add_edge("filter", END)
    return graph.compile(checkpointer=InMemorySaver())


def run_round(
    ctx: ToolContext,
    *,
    sources: Iterable[str] = (),
    headless: bool = False,
    cwd: Path | None = None,
    scorer: Scorer | None = None,
    model: BaseChatModel | None = None,
) -> RoundResult:
    """Run one intake round; in chat, answer each interrupt with `ctx.text()`.

    `model` is the chat model for every call of the round; by default it is built with
    `ctx.model_factory`. Ctrl-C or Ctrl-D at a prompt cancels the round: `profile.md` is
    not written, the result has `cancelled` set and lists the sources handled so far.
    A Ctrl-C during the `filter` node comes after `write` saved the round: its result
    is returned, not cancelled, with a `filter_interrupted` note (the filter file is
    left as it was). Batch-level source errors (`SourcesError`, workspace write
    failures) propagate to the caller.
    """
    from langgraph.types import Command

    if model is None:
        model = ctx.model_factory(ctx.settings, ctx.credentials)
    graph = build_graph(ctx, model, cwd=cwd, scorer=scorer)
    config = {"configurable": {"thread_id": uuid.uuid4().hex}, "recursion_limit": RECURSION_LIMIT}
    payload: RoundState = {"headless": headless, "specs": [s for s in sources if s.strip()]}
    try:
        out = graph.invoke(payload, config)
        while out.get("__interrupt__"):
            request = out["__interrupt__"][0].value
            if request.get("note"):
                ctx.console.print(request["note"], highlight=False, markup=False)
            answer = ctx.text(request["prompt"])
            out = graph.invoke(Command(resume=answer), config)
    except (KeyboardInterrupt, EOFError):
        values = graph.get_state(config).values
        if values.get("result"):  # stopped in `filter`: `write` already saved the round
            result = RoundResult.model_validate(values["result"])
            result.filter_written = False
            result.notes.append(
                RoundNote(
                    kind="filter_interrupted",
                    subject=ctx.msg("intake.origin.filter_sources"),
                    text=ctx.msg("intake.note.filter_interrupted"),
                )
            )
            return result
        return RoundResult(
            cancelled=True,
            sources=[SourceResult.model_validate(s) for s in values.get("sources", [])],
        )
    return RoundResult.model_validate(out["result"])


__all__ = ["build_graph", "classify", "run_round", "words"]
