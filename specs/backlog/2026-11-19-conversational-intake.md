# Conversational intake (follow-up to Phase 2)

Requested by the developer on 2026-11-19 during Phase 2 validation. To be specified as its own feature with `/sdd:feature` after Phase 2 is merged.

## Request
`/add-information` should feel like a fluent conversation, not a form:
- The bot keeps the checklist of things to learn (must-know and nice-to-have, Phase 2 R12).
- For each item it stores whether it is already known and where from (typed in the conversation, a file, a web page, …).
- It guides the user toward missing items and holes in the profile.
- Any message may mix facts about the user with file paths and URLs; paths and URLs are detected and their content gathered automatically, and the rest of the message is used as information about the user.

## Decisions already made
| Decision | Choice | Decided in |
|---|---|---|
| Placement | Separate feature after Phase 2 is merged; Phase 2 closes as built | Phase 2 validation |
| Paths and URLs in messages | Ingested immediately (no confirmation), through the Phase 2 sources module and guard, then reported ("added cv.pdf; filled skills, education") | Phase 2 validation |
| Who steers | The LLM fully drives the conversation (what to ask, when to finish), with the stored item status as context | Phase 2 validation |

## Constraints carried over from Phase 2
- Guarded input (URLs, `.html`) still passes the injection guard before any text reaches the model; the conversation model never sees quarantined content.
- Item status is stored in the workspace (not inferred by the LLM each turn); the merge rule (R13), the filter file (R14), `/show-profile` and the headless `--source` path keep working.
- Reverses the Phase 2 decision "Questions: prewritten per item; the LLM only extracts" — the spec should say what remains as a fallback when the model fails, and how it behaves on small local models.
