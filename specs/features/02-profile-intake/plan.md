# Phase 02: Profile intake and filter file — Plan

## Context
Roadmap Phase 2. Phase 1 left a working chat shell, the tool registry, settings and a verified LLM provider, but the cap knows nothing about the user. This phase lets the user tell the cap who they are, in a dialog and through sources (.txt, .md, .pdf, .html, URLs), and produces a readable `profile/profile.md` plus a generated `listing_filter_file.md` that Phase 3's relevance filter consumes. Because sources may come from the web, this phase also builds the prompt-injection guard and quarantine that every later phase reuses.

## Approach
- **Guard first, fail closed.** `app/guard/` is one facade, `check(content, source) -> Verdict`, combining bilingual deterministic rules with a local ONNX classifier (ProtectAI deberta v2). Without a working classifier, guarded input is refused, never passed on rules alone. Flagged content is encrypted into `quarantine/<id>/` with a metadata-only report, so other agents on the machine cannot read the injected text by accident. Only a human in chat can view (`-show-quarantine`) or release (`-release-quarantine`) it.
- **Sources become files.** Each source gets a folder under `profile/sources/` with the original, the extracted text and a manifest entry keyed by SHA-256, so re-adding is idempotent and dropped-in files are detected.
- **Intake as a LangGraph graph.** `-add-information` drives a graph that ingests sources, takes an introduction and asks prewritten questions for missing checklist items via `interrupt()`. An LLM extraction step with structured output turns text into item values. A deterministic merge (answers replace, sources fill) writes `profile.md`, and a second structured-output step generates `listing_filter_file.md`. The headless path runs the same graph without the dialog nodes.
- **Profile in plain Markdown.** `profile.md` has one section per item with a stable id comment in the heading, so hand edits survive and parsing is deterministic.

## What stays unchanged
- The registry, parser, headless runner semantics, REPL loop and provider code from Phase 1, except for: registering new tools, repeatable list-valued flags in the generated parser (for `--source`, R16), the `incomplete` output flag in the runner (R16), the `model_factory` field on `ToolContext` (T10), the narrowed prompt-path architecture test, the setup flow's final classifier step, the `guard_threshold` setting, workspace notes at startup, and the system prompt and tour texts.
- No vector index, no embedding model, no scraping.

## Group 1: Dependencies, workspace notes and settings
- [x] Add `langgraph`, `llama-index-core`, `llama-index-readers-file`, `beautifulsoup4`, `onnxruntime`, `tokenizers` and `cryptography` (T1); update `uv.lock`; extend `tests/test_project.py` to check they are present and that `torch`, `transformers` and `optimum` are absent; an architecture test asserts no module in `app/` imports `huggingface_hub`
- [x] Narrow `tests/test_architecture.py::test_no_prompt_files_read_outside_texts` to paths under `prompts/`, so workspace file names such as `profile.md` are allowed; workspace note texts in `defaults/workspace/` read through `app/texts.py`
- [x] `ToolContext.model_factory` (default `default_model_factory`) and the same in `tests/helpers.make_ctx`; `ToolOutput.incomplete` (default false), which the headless runner maps to exit 1 after rendering the output
- [x] `Settings.guard_threshold` (float, default 0.5, 0 < t < 1) with validation and a localized error key; shown by `-settings`, settable with `--guard-threshold`
- [x] Workspace `README.md` and `AGENTS.md` (English text in `defaults/workspace/`) written on creation and on start when missing, never overwritten (R18)
- [x] Tests: dependency check; threshold default, validation and `-settings` round trip; notes written on a new workspace, added to an existing one, and an edited note left untouched

## Group 2: Guard rules
- [x] `app/guard/rules.py`: rule set from R3 as data (name, pattern, languages), with case- and diacritics-insensitive matching over normalized text; HTML hidden-text extraction with `beautifulsoup4` (inline style, `hidden`, `aria-hidden`, and class/id rules from the page's own `<style>` blocks, T6) returned separately from visible text; zero-width and bidi control detection
- [x] `app/schemas/guard.py`: `Verdict` (clean/flagged, matched rule names, max score, reason) and `QuarantineReport`
- [x] Tests: each rule matches its Czech and English samples (with and without diacritics), benign Czech and English job-profile texts and HTML fixtures match nothing, hidden-text extraction on fixtures (inline, attribute and `<style>` class-based hiding), zero-width characters inside a word flagged, ordinary Czech typography (non-breaking spaces, en dashes) not flagged

## Group 3: Quarantine store and encryption
- [x] `app/guard/quarantine.py`: create an item (id, `content.enc` via Fernet, `report.json`; 0700 dir, 0600 files), list, decrypt to memory, mark released and delete `content.enc`; key generated on first use and stored as `SORTINGCAP_QUARANTINE_KEY` through `app/credentials.py` (T5)
- [x] `-show-quarantine [<id>]` tool (chat-only, user-only): list, or decrypt and print as plain text with control and bidi characters escaped (R6)
- [x] Tests: round trip; files and modes; no plaintext of the content anywhere under the workspace after quarantining (byte search); report has no excerpt; key in `.env` 0600 and never in output, logs or `settings.json`; missing key with existing items gives the clear error and deletes nothing; `-show-quarantine` refused headless (exit 2) and absent from the agent's tools; markup-like and bidi content printed literally

## Group 4: Classifier, download and guard facade
- [ ] `app/guard/classifier.py`: pinned repo, revision, file list and SHA-256 values (T3); `is_installed()`, `download(ctx)` via `httpx` with progress, temp file and rename after the checksum; load with `onnxruntime` CPU and `tokenizers`; windowed scoring (T4) behind a small `Scorer` protocol so tests inject a fake
- [ ] `app/guard/__init__.py` `check()`: rules plus classifier on visible and hidden text; fails closed with a `GuardUnavailable` user-facing error when the classifier is missing, has a bad checksum or does not load (R4); flagged input quarantined and the verdict returned
- [ ] Download offers: a final confirmed step in `-setup` after successful verification (declining keeps setup successful), and an `ensure_classifier(ctx)` helper that offers it in chat and fails closed headless (R8)
- [ ] Tests: windowing (510 text tokens + `[CLS]`/`[SEP]`, 64 overlap) and max-score aggregation with a fake scorer; checksum verified once per process; threshold from settings; checksum mismatch rejects and removes the temp file; the download never runs headless or without a yes; a corrupt model file fails closed; the `-setup` step offered once after success; the chat offer on the first guarded input while the classifier is missing (yes downloads, no refuses that input); the headless hint to run `-setup`; the guard quarantines on a rule hit, on a score hit and on a hidden-text hit; a clean verdict for benign fixtures

## Group 5: Sources, readers and manifest
- [ ] `app/intake/sources.py`: resolve paths and URLs; readers per type (T6); URL fetch with limits, a content-type check and redirect handling; guard on URLs and `.html` before any text extraction; copy to `profile/sources/<slug>/` (`original.*`, `source.url`, `text.md`), manifest with SHA-256 dedupe and a `cv` flag (R10); a dropped-in file scan (R11), with flagged drop-ins moved into the quarantine
- [ ] Per-source result model (added, skipped duplicate, quarantined, refused-no-classifier, error with reason)
- [ ] Tests: each reader on fixtures (including a cp1250 `.txt` and a small PDF); a URL via faked HTTP, including a redirect, an oversize body, a non-HTML type, an error status and a timeout; an unsupported type, an unreadable file and empty extracted text each reported per source without ending the batch; duplicate skipped; a dropped-in file picked up once; a flagged drop-in moved to quarantine and gone from `sources/`; without the classifier, `.html` and URLs are refused while `.txt`/`.pdf` still work; nothing from a flagged source written outside the quarantine

## Group 6: Profile model, Markdown I/O and `-show-profile`
- [ ] `app/intake/checklist.py`: item ids, order, must/nice grouping, localized titles and prewritten questions (message keys) (R12)
- [ ] `app/intake/profile.py`: parse and render `profile.md` with id comments (T7), unknown sections kept verbatim, the CV section rendered from the manifest, missing-item detection, and the merge rule with conflicts (R13)
- [ ] `-show-profile` tool (agent-callable, headless allowed) (R17)
- [ ] Tests: render/parse round trip in cs and en; a hand-edited title and body kept; unknown sections kept; a translated title still parsed; merge cases (answer replaces, source fills, conflict listed); CV satisfied by a flagged source; `-show-profile` output in both languages, headless `--json`, and still working when `sources/*/text.md` and `quarantine/` are unreadable (R17); the agent's tool list is exactly `help`, `tour`, `show-profile` (T11)

## Group 7: Extraction and the intake graph
- [ ] `tests/helpers.py`: a fake chat model that supports `with_structured_output` (returns queued Pydantic objects or raises), alongside the Phase 1 `FakeToolModel`
- [ ] `app/schemas/intake.py`: extraction output model with per-item relation `match`/`adds`/`contradicts` (T8), round state and summary models
- [ ] `prompts/{cs,en}/intake/extract.md`
- [ ] `app/intake/extract.py`: structured-output call with the character budget and the model-error fallbacks (T8)
- [ ] `app/intake/graph.py`: LangGraph `StateGraph` with sources → intro → question loop (`interrupt()`) → merge → write; `next`/`done`/empty handling with localized words (R9); a headless mode without the dialog nodes (T10)
- [ ] Tests with a fake structured-output model: a full round answering some questions, skipping with `next` and ending with `done`; must-know questions before nice-to-have; already-filled items not asked; a source fills empty items, `adds` appends a paragraph, `match` changes nothing and `contradicts` becomes a conflict; a cancelled round or a failed source extraction leaves `extracted` false and the next round (chat or headless) extracts it from `text.md` without refetching; the CV question with a path flags the source; a model error on an answer keeps the text verbatim and on a source skips it with a note; a text over 12 000 characters is cut and the cut noted; Ctrl-C mid-round leaves `profile.md` as it was before the round

## Group 8: Filter file, `-add-information` tool and round summary
- [ ] `prompts/{cs,en}/filter/generate.md`; `app/intake/filter_file.py`: structured output into `FilterFile`, rendered with the generated-file header, written with `write_private` (R14, T9); regenerated only when the profile changed or the file is missing
- [ ] `-add-information` tool (`app/tools/add_information.py`, not agent-callable): in chat runs the dialog graph; `--source` (repeatable) for the headless path (R16); no provider/model → `-setup` hint, checked before any source is copied; refused guarded sources set `incomplete`; the summary with all file paths (R15)
- [ ] Generated parser: list-valued input fields become repeatable flags (`--source a --source b`), in chat and headless alike
- [ ] Tests: repeatable `--source` parsed in chat and headless; filter file sections and header in cs and en; overwritten each round, not regenerated when nothing changed, regenerated when the file is missing; the summary lists every file path, conflicts and each source outcome (quarantined ones with id, source and reason); headless `run add-information --source a.txt --source https://…` with faked HTTP and model (exit 0), without `--source` only dropped-in files processed, with a guarded source and no classifier (exit 1, others processed), without a model (exit 1)

## Group 9: Quarantine release
- [ ] `-release-quarantine <id>` tool (chat-only, user-only): report and text as in `-show-quarantine`, confirm default no, ingest via the sources module without re-guarding, mark released and delete `content.enc` (R7)
- [ ] Tests: release confirm/decline, release refused headless with and without `--yes` (exit 2), not agent-callable, released content appears in `sources/` and the manifest, and a second release of the same id fails

## Group 10: Texts, docs, constitution and live eval
- [ ] cs/en messages for all new tools, flags, questions and summaries; system prompt and tour "next steps" mention `-add-information` and `-show-profile` (R19)
- [ ] README: `-add-information` (dialog and headless), sources, profile files, filter file (generated only), `-show-profile`, the guard, classifier download, quarantine (encrypted; `-show-quarantine`, `-release-quarantine`), workspace notes; CHANGELOG entries
- [ ] Amend `specs/mission.md` and `specs/tech-stack.md` per T13
- [ ] `tests/live/` with a `live` pytest marker deselected by default (`addopts`), the real classifier on Czech and English injection and benign samples, precision and recall printed per language (T12)
- [ ] Tests: message key parity cs/en; every prompt file reachable via `app/texts.py`; `pytest` without `-m live` collects no live test

## Edge cases
- The classifier is missing during a chat round with a URL → download offered; on no, that source is refused and the round continues with the others.
- A URL redirects to another host → the final URL is recorded in `source.url`; the guard checks the final content.
- An HTML page whose visible text is clean but hidden text has an injection → quarantined.
- The same CV is added twice under different names → the SHA-256 match skips it.
- `profile.md` deleted by hand → treated as empty; the next round recreates it.
- `profile.md` with a section whose id comment was removed → kept as an unknown section; the item counts as missing and is asked again.
- Ctrl-C or Ctrl-D during a question → the round is cancelled; sources already copied stay, and the profile and filter file are unchanged.
- The quarantine key is removed from `.env` → listing still works (reports are plaintext); viewing and release fail with a message; nothing is deleted.
- A released item's source is a dropped-in `.html` → ingested from the decrypted bytes; the original file is no longer in `sources/`, so it is not re-scanned.
- A very long PDF → text cut at the budget and noted in the summary.
- The model returns invalid structured output → handled like a model error (T8).
