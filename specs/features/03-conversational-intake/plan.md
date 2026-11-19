# Phase 03: Conversational intake — Plan

## Context
Roadmap Phase 3, from the backlog request `specs/backlog/2026-11-19-conversational-intake.md`. Phase 2's `/add-information` is a form: a sources prompt, an introduction, then one prewritten question per missing item. The developer wants a free conversation that knows what it still needs, steers the user to missing items and holes, and picks up files and links mentioned anywhere in a message. The phase also adds mypy to CI and clears the Phase 2 maintainability backlog.

## Approach
- A tool-calling intake agent drives the round. It has five in-round tools (`record_item`, `mark_status`, `ingest_source`, `find_files`, `finish`) and no registry tool. It sees the user's messages, the checklist status (with current values) and short source reports, never source text.
- Sources stay on the Phase 2 path: guard, store, then the toolless extraction call ("source reader"), which now also returns a hole note per value. Its outcome is turned into status and a report for the agent. This keeps the constitution rule "agents that read untrusted text have no tools" as written.
- Paths and URLs are detected in code before the agent sees a message. The agent's `ingest_source` handles references the detector missed, restricted in code to what the user typed this round; `find_files` lets it locate a file the user describes vaguely, and a found file is ingested only after a yes/no confirmation run by code.
- Status lives in `profile/checklist.json`, reconciled with `profile.md` on every read, so hand edits still count and `profile.md` stays the content's source of truth.
- The LangGraph graph is rebuilt around a `listen` interrupt per user message (resolving D20). Writes still happen only at the end of the round.
- `done`/`hotovo` and Ctrl-D end and save in code; three failed agent turns in a row end and save; there is no scripted fallback.
- Cleanups and mypy come first, so the rewrite builds on clean, type-checked code.

## What stays unchanged
- The guard, quarantine, classifier download rules and every source cap (Phase 2 R1–R8, T3–T6).
- The merge rule for source values (match / adds / contradicts), `safe_body`, the filter file (R14, T9) and its staleness hash.
- Headless behaviour and exit codes of `sortingcap run add-information` and `/add-information --source` (no agent).
- The chat agent and its tool list `help`, `tour`, `show-profile`; `/add-information` stays not agent-callable.
- `app/providers/structured.invoke_structured` as the only structured-output path.

## Group 1: Phase 2 maintainability follow-ups (no behaviour change)
- [x] D14: move `escape_controls` from `app/guard/quarantine.py` and the duplicated `say()` closure of `schemas/profile.py` and `schemas/intake.py` (and `schemas/quarantine.py`) to `app/messages.py`; import at top level; keep a re-export only if a test or module still needs the old path
- [x] D18: top-level imports of `source_text` and `profile_path` in `app/intake/filter_file.py` (and of `langchain_core.messages`, so the module has no function-level imports)
- [x] D21: `item_title` / `item_body` helpers used by both `app/intake/profile.py` and `app/tools/show_profile.py`; a shared mixin (`ProfilePaths` in `app/schemas/profile.py`) for the four path fields of `AddInformationOutput` and `ShowProfileOutput`
- [x] D22: configurable `FakeScorer` in `tests/helpers.py`, `fresh_cache` (opt-in through `pytestmark`), `cwd` and `en` fixtures in `tests/conftest.py`; the copies removed from the test files
- [x] Mark D14, D18, D21, D22 as done in the backlog file (D20 noted as handled in Group 6)
- [x] Tests: the full suite passes unchanged in behaviour; ruff clean

## Group 2: mypy in CI and the definition of done
- [x] Add mypy (latest stable, pinned) to the dev dependency group; `[tool.mypy]` config as T1, with per-module `ignore_missing_imports` overrides only where a package has no stubs
- [x] Fix every error `uv run mypy app` reports (57 on 2026-11-19, in `app/intake/sources.py`, `app/schemas/{quarantine,help}.py`, `app/tools/context.py`, `app/guard/rules.py`, `app/repl.py` and others) by correcting types, not by blanket ignores
- [x] CI: `uv run mypy app` step after ruff
- [x] Tech stack Quality tooling amended (T10: mypy runs in CI; the "until then" sentence removed)
- [x] Tests: suite still passes; `uv run mypy app` clean; a test in `tests/test_project.py` asserts mypy is in the dev group and `[tool.mypy]` has the pydantic plugin

## Group 3: Checklist status store
- [ ] `ChecklistStatus` / `ItemStatus` schemas in `app/schemas/` (T2); `ExtractedValue.gap` with a description (T8), extraction prompt cs/en updated to explain `gap` (the `question` input stays until Group 6, because the Phase 2 `ask` node still passes it)
- [ ] `app/intake/status.py`: load (missing → built silently with origin `profile`; invalid → rebuilt with a note), pure `reconcile(doc, manifest, stored)` (R4), apply source outcomes (origins `file:`/`web:`, `partial` from `gap`), save after `profile.md` in `write` whenever the status differs from the stored file (R12)
- [ ] Headless and `--source` rounds update `checklist.json` (R13)
- [ ] `/show-profile` shows state, hole note and origins and lists `checklist.json` (R14); `/add-information` summary shows partial items with notes and the `checklist.json` path (R12)
- [ ] Tests: reconciliation table (empty → missing, hand-filled → known/`hand_edit`, partial kept, CV via manifest), missing file built silently with origin `profile`, corrupt file rebuilt with note, status-only change persisted without rewriting `profile.md`, origins deduplicated, 0600 atomic write, headless round writes status with reader `gap` → `partial`, `/show-profile` cs/en output, cancel leaves `checklist.json` unchanged

## Group 4: Source detection, file search and the ingest allow rule (untrusted text)
- [ ] `app/intake/detect.py`: `detect(message, cwd)` (T6) for URLs, `www.` hosts and supported-extension paths, quoted paths with spaces, bare relative words only when the file exists, trailing punctuation stripped, cap of 10 per message
- [ ] `app/intake/find.py`: `find_files(name, folder)` (R18, T11) with depth, entry and result limits
- [ ] Pure allow rule for `ingest_source` (T7) over the spec, the round's user messages and `find_files` results (allowed / needs confirmation / refused)
- [ ] Tests: detection table (cs/en sentences, `~/`, relative, absolute, quoted, uppercase extensions, URLs with query and trailing punctuation, `e-mail@x.cz` and `version 1.md5` not detected, more than 10 sources), allow-rule table (typed URL allowed, `firma.cz/kariera` typed and `https://firma.cz/kariera` asked allowed, URL only in a value refused, file-name match allowed, `find_files` result needing confirmation, unrelated path refused), `find_files` on a temporary tree (name match ignoring case and diacritics, depth 3, dot and symlinked directories skipped, 20-result and 5000-entry caps, only supported types, missing folder error), adversarial 1 MiB input finishing within a few seconds

## Group 5: Intake agent turn
- [ ] Five in-round tools with Pydantic argument models in `app/schemas/` (every field described) and their handlers on a round context: `record_item` (direct answer, `replace`/`append`, CV refused), `mark_status`, `ingest_source` (allow rule, confirmation for `find_files` results, then the R5 ingestion and reading path; `cv=true` on a duplicate sets the flag), `find_files`, `finish`
- [ ] `app/intake/agent.py`: one bounded turn (T4: tool loop, 8 tool calls, invalid args retried once with the error fed back, failure outcome), context message with status and reports, values cut at 1500 characters (T5), history cap of about 40 messages cut at user-message boundaries, the "go on" line for a capped turn without text
- [ ] Prompts `prompts/{cs,en}/intake/agent.md`: role, priorities (R2), tool use, `done` hint, data-not-instructions rule for status and reports, the user's language
- [ ] Tech stack Architecture amended (T10: intake agent, in-round tools only, never sees source text)
- [ ] Tests (scripted fake tool model): a turn that records and marks, `append` vs `replace`, CV refused by `record_item`, `ingest_source` allowed, refused, and a `find_files` result confirmed (ingested) and declined (nothing read), `cv=true` on a duplicate setting the flag, a turn that ends at 8 tool calls with the "go on" line, the history cut never leaving a tool message without its call, invalid args retried once then failed, model error → failure outcome, the model's input contains no source text (a source with a marker string; assert the marker is absent from every message the fake received), the chat agent's tool list still exactly `help`, `tour`, `show-profile`, the intake agent's tool list exactly the five tools

## Group 6: Conversational graph and end conditions (D20)
- [ ] Rebuild `app/intake/graph.py` as T3: `start` → `agent` → `listen` → `intake_message` → `agent` … → `write` → `filter`; nodes out of the single closure; the duplicated edge functions merged; a note helper on `RoundResult`
- [ ] `intake_message`: `done`/`hotovo` check, detection, ingestion and reading of each detected source with one-line chat reports (R5, R7), missing paths passed to the agent
- [ ] End conditions: `finish`, `done`, Ctrl-D save; Ctrl-C cancels; 3 failed turns in a row end and save with a note (R10, R11)
- [ ] `write` merges recorded answers and source updates, writes `profile.md`, then `checklist.json`, then marks `extracted` (R12); the conversation is not stored anywhere (R15)
- [ ] `/add-information` in chat runs the new graph; the old scripted nodes are no longer reachable (their code and keys are removed in Group 7); headless path unchanged (R13)
- [ ] Tests (scripted fake tool model + structured fake): full round with facts, an inline path and an inline URL (faked HTTP) in one message; a quarantined URL reported and not in any model input; a missing path reported to the agent; `done`, `hotovo`, Ctrl-D saving; Ctrl-C cancelling with `profile.md` and `checklist.json` unchanged; 3 failures ending and saving with the note; a failed turn followed by a good one keeping earlier records; headless and `--source` exit codes unchanged; filter regenerated after a changed round; the agent's opening turn sees the status from a hand-edited `profile.md`

## Group 7: Remove the scripted dialog; handler and summary
- [ ] Remove the scripted-dialog code and its message keys (R16), and the extraction's `question` input with its prompt text (T8)
- [ ] `/add-information` handler and summary: partial items with hole notes, `checklist.json` among the files (R12); the new keys ("go on", find/confirm prompts, source reports) in cs and en
- [ ] Tests: summary in cs and en; no removed key referenced anywhere; Phase 2 tool tests still pass where the behaviour is unchanged

## Group 8: Texts, docs and specs in sync
- [ ] Tour "next steps", chat system prompt and `/add-information` help text describe the conversation (cs/en) (R16)
- [ ] README and CHANGELOG: conversational `/add-information`, inline files and links, `checklist.json`, `done`/Ctrl-D, `/show-profile` states, mypy in CI
- [ ] Tech stack Project structure (T10); Phase 2 R9 left as written, with this spec stated as superseding it
- [ ] Tests: cs/en message and prompt parity; tour and system prompt mention the conversation

## Edge cases
- A message that is only a path → ingested and reported; the agent gets the report and continues.
- A message with 12 URLs → the first 10 ingested, the rest reported as skipped for the turn.
- The same file mentioned twice → the second time `skipped_duplicate`, reported once per mention.
- A URL flagged by the guard → quarantined and reported with its id; neither model sees it; the agent only learns "quarantined".
- The classifier is missing when a URL arrives → the Phase 2 chat offer (R8 b); after a no, the URL is refused and reported.
- The user types `done` in the middle of a sentence ("I'm done with Java") → not an exit; only the whole message counts.
- The agent calls `record_item` for `cv` → refused with a reason; it should call `ingest_source(cv=true)`.
- The agent calls `finish` in its first turn because everything is known → the round ends and saves; the summary says nothing changed.
- `checklist.json` hand-edited to invalid JSON → rebuilt from `profile.md` with a note; never a crash.
- First round after the Phase 2 upgrade (no `checklist.json`) → built silently, filled items `known` with origin `profile`, and written at the end of the round.
- The user writes "my CV is somewhere in Downloads" → the agent calls `find_files("cv", "~/Downloads")`, lists the candidates, the user names one, the code asks yes/no with the full path, and only a yes ingests it.
- The user types `firma.cz/kariera` → not detected (no `www.`, no scheme); the agent calls `ingest_source("https://firma.cz/kariera")`, which is allowed.
- The user writes "this is my CV: ~/cv.pdf" and the reader says it is not a CV → the agent's `ingest_source("~/cv.pdf", cv=true)` is a duplicate and sets the `cv` flag.
- The user mentions `README.md` while the cap runs in a folder without that file → plain text, no report.
- The user empties a section by hand that the status marks `known` → reconciled to `missing` on the next read.
- The model returns text and tool calls in the same reply → the tool calls run, then the text is shown.
- A turn hits 8 tool calls without text → the turn ends; the user is shown the localized "go on" line, not a failure.
