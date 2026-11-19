# Phase 2 maintainability follow-ups

Deferred from the Phase 2 (`02-profile-intake`) deep review on 2026-11-19. None changes behaviour.

- [x] **D14** (done in Phase 3 Group 1: `escape_controls` and `say(console, text, style)` in `app/messages.py`) `escape_controls` (terminal-safety helper) lives in `app/guard/quarantine.py` and is imported lazily by `app/schemas/{profile,intake,quarantine}.py`; the same `say()` closure is copied in `schemas/profile.py` and `schemas/intake.py`. Move both to `app/messages.py` (or `app/tools/context.py`).
- [x] **D18** (done in Phase 3 Group 1) `app/intake/filter_file.py` imports `source_text` and `profile_path` inside functions though there is no import cycle. Use top-level imports.
- [ ] **D20** (handled by the Phase 3 graph rewrite, `03-conversational-intake` Group 6) `app/intake/graph.py` `build_graph` is one ~250-line closure; `after_start` and `after_sources` are identical; `filter_node` builds `RoundNote`s by hand. Merge the edge functions, add a note helper usable on `RoundResult`, consider lifting the nodes into a small class.
- [x] **D21** (done in Phase 3 Group 1: `item_title` / `item_body` in `app/intake/profile.py`, mixin `ProfilePaths` in `app/schemas/profile.py`) Item title/body rules are duplicated in `app/intake/profile.py` and `app/tools/show_profile.py`. Add `item_title` / `item_body` helpers; optionally a mixin for the four path fields shared by `AddInformationOutput` and `ShowProfileOutput`.
- [x] **D22** (done in Phase 3 Group 1) Tests: `FakeScorer` and the `fresh_cache` fixture are copied in four test files, `cwd` and `en` fixtures in three. Move a configurable `FakeScorer` to `tests/helpers.py` and the fixtures to `tests/conftest.py`.

Also noted during validation (not a review finding): the project has no typechecker; an ad-hoc mypy run reports errors in `app/intake/sources.py`, `app/schemas/{quarantine,help}.py`, `app/tools/context.py` and `app/guard/rules.py`. Consider adding mypy or pyright to the checks.
