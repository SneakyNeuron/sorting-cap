"""The intake round as a LangGraph `StateGraph` (Phase 3 R1, R5, R10–R13, R15, T3).

    start → agent → listen → intake_message → agent … → write → filter
    start → write → filter                                          (headless)

`start` picks up dropped-in files, reads `profile.md` and the reconciled checklist
status, ingests the `--source` specs (headless) and reads every source not yet
extracted with the toolless reader (R7). `agent` runs one intake-agent turn
(`app/intake/agent.py`), `listen` waits for the user's next message with
`interrupt()`, and `intake_message` ends the round on `done` (cs also `hotovo`) or
Ctrl-D, else detects the paths and URLs in the message and ingests and reads each one,
with a one-line chat report (R5); the agent then gets the message and the reports.

The round ends when the agent calls `finish`, the user types `done` or presses
Ctrl-D, or `MAX_FAILURES` agent turns fail in a row (with a summary note, R11);
every one of these saves. Ctrl-C cancels: `profile.md` and `checklist.json` are left
as they were, and copied sources stay unextracted, so the next round reads them.

Nothing is written to `profile.md` before `write`. `write` merges the direct answers
and the source values, saves `profile.md`, then `profile/checklist.json` whenever the
status differs from the stored file, and only then marks the extracted sources (R12).
`filter` regenerates `listing_filter_file.md` when the profile changed or the file is
missing; on a model error, or a Ctrl-C during that call, the previous file is kept and
the summary notes it.

The state is plain JSON (`RoundState`): the round's `RoundData` and the conversation
as message dicts, held only by the round's in-memory checkpointer; nothing of the
conversation is written anywhere (R15). Every node sets `goto`, read by the one edge
function.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.intake.agent import TurnOutcome, bind, run_turn
from app.intake.checklist import CV_ITEM, ITEMS
from app.intake.detect import MAX_SOURCES, detect, split_cap
from app.intake.extract import CHAR_BUDGET
from app.intake.filter_file import generate, needs_filter, stored_texts, write_filter
from app.intake.profile import cv_body, merge, missing_items, profile_path, save_profile
from app.intake.round import RoundContext
from app.intake.sources import (
    SourcesError,
    ingest_sources,
    load_manifest,
    mark_extracted,
    scan_dropped,
    set_cv,
    unextracted,
)
from app.intake.status import holes, load_status, now_utc, save_status
from app.messages import escape_controls, say, short_error
from app.schemas.intake import RoundResult, RoundState
from app.schemas.intake_agent import RoundData, SourceReport
from app.schemas.sources import SourceResult

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import BaseMessage

    from app.guard import Scorer
    from app.tools.context import ToolContext

RECURSION_LIMIT = 10_000
MAX_FAILURES = 3  # agent turns failed in a row that end (and save) the round (R11)
# resumes `listen` for Ctrl-D (`Command(resume=None)` would not resume the graph)
EOF: dict[str, bool] = {"eof": True}


def words(ctx: ToolContext, key: str) -> set[str]:
    """A comma-separated word list from the catalog, case-folded."""
    return {w.strip().casefold() for w in ctx.msg(key).split(",") if w.strip()}


def is_done(ctx: ToolContext, message: str) -> bool:
    """The whole message is `done` (cs also `hotovo`), ignoring case and spaces (R10)."""
    return message.strip().casefold() in words(ctx, "intake.words.done")


def route(state: RoundState) -> str:
    """The one edge function: every node names the node to run next."""
    return state.get("goto") or "write"


class IntakeNodes:
    """The graph's nodes over one tool context and chat model (D20). The model is the
    intake agent (bound to its tools per turn) and, unbound, the source reader and the
    filter generator."""

    def __init__(
        self,
        ctx: ToolContext,
        model: BaseChatModel,
        *,
        cwd: Path | None = None,
        scorer: Scorer | None = None,
        home: Path | None = None,
    ) -> None:
        self.ctx = ctx
        self.model = model
        self.cwd = cwd
        self.scorer = scorer
        self.home = home

    def round(self, state: RoundState) -> RoundContext:
        data = RoundData.model_validate(state.get("round") or {})
        return RoundContext(self.ctx, self.model, data, self.cwd, self.scorer, self.home)

    # --- start ---------------------------------------------------------------------

    def start(self, state: RoundState) -> dict[str, Any]:
        ctx = self.ctx
        headless = bool(state.get("headless"))
        dropped = scan_dropped(ctx, scorer=self.scorer)
        round_ = RoundContext.open(
            ctx, self.model, cwd=self.cwd, scorer=self.scorer, home=self.home
        )
        stored = load_status(ctx.workspace).stored
        data = round_.data
        data.sources.extend(dropped)
        if headless:
            data.sources.extend(
                ingest_sources(ctx, state.get("specs", []), cwd=self.cwd, scorer=self.scorer)
            )
        else:  # drop-ins that were not stored are reported like a detected source
            for result in dropped:
                if result.status in ("added", "skipped_duplicate"):
                    continue
                report = SourceReport(origin=result.origin, status=result.status, error=result.code)
                data.reports.append(report)
                round_.announce(result, report)
        by_slug = {r.slug: r for r in dropped if r.status == "added" and r.slug}
        for entry in unextracted(ctx.workspace):
            report = round_.read_stored(entry)
            if not headless:
                data.reports.append(report)
                result = by_slug.get(entry.slug) or SourceResult(
                    origin=entry.origin, status="added", type=entry.type, slug=entry.slug
                )
                round_.announce(result, report)
        return {
            "round": data.model_dump(mode="json"),
            "history": [],
            "message": None,
            "failures": 0,
            "status_stored": stored.model_dump(mode="json") if stored else None,
            "profile_existed": profile_path(ctx.workspace).exists(),
            "goto": "write" if headless else "agent",
        }

    # --- conversation --------------------------------------------------------------

    def agent(self, state: RoundState) -> dict[str, Any]:
        """One agent turn. A failed turn (R11) keeps everything recorded and gives the
        source reports it consumed back to the next turn; an `OSError` or a manifest
        error inside a tool counts as a failed turn. Ctrl-D at a confirmation inside
        the turn ends and saves the round, keeping what the turn recorded."""
        from langchain_core.messages import HumanMessage, messages_from_dict, messages_to_dict

        ctx = self.ctx
        round_ = self.round(state)
        history = messages_from_dict(state.get("history", []))
        message = state.get("message")
        pending = list(round_.data.reports)
        try:
            outcome = run_turn(round_, bind(self.model, round_), history, message)
        except EOFError:
            return {"round": round_.data.model_dump(mode="json"), "goto": "write"}
        except (OSError, SourcesError) as exc:
            detail = exc.text(ctx.texts) if isinstance(exc, SourcesError) else short_error(exc)
            say(ctx.console, ctx.msg("intake.agent.failed", detail=detail), "yellow")
            kept: list[BaseMessage] = [HumanMessage(message)] if message is not None else []
            outcome = TurnOutcome("failed", error=detail, messages=kept)
        failures = int(state.get("failures", 0))
        if outcome.failed:
            failures += 1
            # the agent never saw these reports: show them in the next context
            round_.data.reports = [*pending, *round_.ingested]
        else:
            failures = 0
        goto = "listen"
        if round_.data.finished:
            goto = "write"
        elif failures >= MAX_FAILURES:
            round_.note(
                "agent_failed",
                ctx.msg("intake.origin.agent"),
                ctx.msg("intake.note.agent_failed", count=failures),
            )
            goto = "write"
        return {
            "round": round_.data.model_dump(mode="json"),
            "history": messages_to_dict([*history, *outcome.messages]),
            "message": None,
            "failures": failures,
            "goto": goto,
        }

    def listen(self, state: RoundState) -> dict[str, Any]:
        """Wait for the user's next message; `run_round` resumes with the text, or with
        `EOF` for Ctrl-D. Nothing else happens here, as the node reruns on resume."""
        from langgraph.types import interrupt

        answer = interrupt({"prompt": self.ctx.msg("intake.agent.prompt")})
        return {"message": answer if isinstance(answer, str) else None, "goto": "intake_message"}

    def intake_message(self, state: RoundState) -> dict[str, Any]:
        """`done` / Ctrl-D end the round without a model call (R10); an empty message
        is ignored; otherwise every detected source is ingested and read, reported in
        one chat line, and its report kept for the agent (R5). Sources beyond
        `MAX_SOURCES` are reported as skipped. Ctrl-D at a prompt during ingestion
        (the classifier offer) ends and saves the round."""
        ctx = self.ctx
        message = state.get("message")
        if message is None or is_done(ctx, message):
            return {"message": None, "goto": "write"}
        if not message.strip():
            return {"message": None, "goto": "listen"}
        round_ = self.round(state)
        take, skipped = split_cap(detect(message, self.cwd or Path.cwd()))
        goto = "agent"
        try:
            for found in take:
                result, report = round_.ingest(found.normalised)
                round_.data.reports.append(report)
                round_.announce(result, report)
        except EOFError:
            goto = "write"
        else:
            for found in skipped:
                text = ctx.msg(
                    "intake.report.skipped_cap",
                    origin=escape_controls(found.spec),
                    limit=MAX_SOURCES,
                )
                say(ctx.console, text, "yellow")
        return {"round": round_.data.model_dump(mode="json"), "goto": goto}

    # --- end of the round ----------------------------------------------------------

    def write(self, state: RoundState) -> dict[str, Any]:
        ctx = self.ctx
        round_ = self.round(state)
        data = round_.data
        base = data.base
        new_doc, merged = merge(
            base,
            answers={k: v for k, v in data.answers.items()},
            updates=data.updates,
            texts=ctx.texts,
        )
        for slug in data.cv_slugs:
            set_cv(ctx.workspace, slug)
        status = round_.status(now_utc())
        manifest = load_manifest(ctx.workspace)
        status_written = status.model_dump(mode="json") != state.get("status_stored")
        cv_changed = cv_body(manifest).strip() != base.body(CV_ITEM).strip()
        changed = list(merged.changed)
        if cv_changed:
            changed = [i.id for i in ITEMS if i.id in changed or i.id == CV_ITEM]
        written = bool(changed or data.extracted or not state.get("profile_existed", False))
        if written:
            save_profile(ctx.workspace, new_doc, ctx.texts, manifest)
        if status_written:  # after `profile.md`, also when only the status changed (R12)
            save_status(ctx.workspace, status)
        if written:
            mark_extracted(ctx.workspace, data.extracted)
        result = RoundResult(
            sources=data.sources,
            changed=changed,
            missing=missing_items(new_doc, manifest),
            partial=holes(status),
            conflicts=merged.conflicts,
            notes=data.notes,
            extracted=data.extracted if written else [],
            profile_written=written,
            status_written=status_written,
            profile_changed=bool(changed),
        )
        return {"result": result.model_dump(mode="json"), "goto": "filter"}

    def filter(self, state: RoundState) -> dict[str, Any]:
        ctx = self.ctx
        result = RoundResult.model_validate(state["result"])
        if not needs_filter(ctx.workspace, profile_changed=result.profile_changed):
            return {"goto": "__end__"}
        sources, unreadable = stored_texts(ctx.workspace, load_manifest(ctx.workspace))
        for origin in unreadable:
            result.note(
                "filter_text_missing",
                origin,
                ctx.msg("intake.note.filter_text_missing", origin=origin),
            )
        outcome = generate(self.model, ctx.texts, ctx.workspace, sources=sources)
        subject = ctx.msg("intake.origin.filter_sources")
        if outcome.truncated:
            result.note(
                "truncated",
                subject,
                ctx.msg("intake.note.truncated", origin=subject, limit=CHAR_BUDGET),
            )
        if outcome.filter is None:
            result.note(
                "model_error_filter",
                subject,
                ctx.msg("intake.note.model_error_filter", detail=outcome.error),
            )
        else:
            write_filter(
                ctx.workspace,
                outcome.filter,
                ctx.texts,
                profile_sha256=outcome.profile_sha256 or "",
            )
            result.filter_written = True
        return {"result": result.model_dump(mode="json"), "goto": "__end__"}


def build_graph(
    ctx: ToolContext,
    model: BaseChatModel,
    *,
    cwd: Path | None = None,
    scorer: Scorer | None = None,
    home: Path | None = None,
):
    """The compiled intake graph with an in-memory checkpointer (T3)."""
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph

    nodes = IntakeNodes(ctx, model, cwd=cwd, scorer=scorer, home=home)
    graph = StateGraph(RoundState)
    targets = {
        "start": ["agent", "write"],
        "agent": ["listen", "write"],
        "listen": ["intake_message"],
        "intake_message": ["agent", "listen", "write"],
        "write": ["filter"],
        "filter": [END],
    }
    for name in targets:
        graph.add_node(name, getattr(nodes, name))
    graph.add_edge(START, "start")
    for name, ends in targets.items():
        graph.add_conditional_edges(name, route, ends)
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
    """Run one intake round; in chat, each user message is read with `ctx.text()`.

    `model` is the chat model for every call of the round; by default it is built with
    `ctx.model_factory`. Ctrl-D at the message prompt ends and saves the round like
    `done` (R10). Ctrl-C at any prompt or during a model call cancels it: `profile.md`
    and `checklist.json` are not written, the result has `cancelled` set and lists the
    sources handled so far (a Ctrl-D before the conversation starts, at a prompt of
    `start`, cancels too). A Ctrl-C during the `filter` node comes after `write` saved
    the round: its result is returned, not cancelled, with a `filter_interrupted` note
    (the filter file is left as it was). Batch-level source errors (`SourcesError`,
    workspace write failures outside an agent turn) propagate to the caller.
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
            answer: str | dict[str, bool]
            try:
                answer = ctx.text(request["prompt"])
            except EOFError:  # Ctrl-D: end and save (R10)
                answer = EOF
            out = graph.invoke(Command(resume=answer), config)
    except (KeyboardInterrupt, EOFError):
        values = graph.get_state(config).values
        if values.get("result"):  # stopped in `filter`: `write` already saved the round
            result = RoundResult.model_validate(values["result"])
            result.filter_written = False
            result.note(
                "filter_interrupted",
                ctx.msg("intake.origin.filter_sources"),
                ctx.msg("intake.note.filter_interrupted"),
            )
            return result
        data = RoundData.model_validate(values.get("round") or {})
        return RoundResult(cancelled=True, sources=data.sources)
    return RoundResult.model_validate(out["result"])


__all__ = ["MAX_FAILURES", "IntakeNodes", "build_graph", "is_done", "route", "run_round", "words"]
