# Tech stack

## Runtime & language
- Python 3.12, managed by uv (project, virtualenv, Python install and lockfile). The app is started with `uv run`.
- Dependencies are declared in `pyproject.toml` and pinned exactly in `uv.lock`. Exact versions are chosen and verified when the phase that introduces a dependency is planned.

## Frameworks & libraries
| Concern | Choice | Why |
|---|---|---|
| Agents & orchestration | LangGraph (`langgraph` 1.x) on LangChain | Stateful graphs for intake (`interrupt()` for questions), scraping pipeline and the reflection loop; LangChain tool and model abstractions. |
| Document ingestion & retrieval | LlamaIndex (`llama-index-core`, `llama-index-readers-file`), `beautifulsoup4` | LlamaIndex reads PDFs now and builds the local index the fact-checker queries later (phase 7); `beautifulsoup4` extracts visible and hidden HTML text. |
| Schemas | Pydantic v2 | Every structured LLM output, every tool input/output and every stored record is a Pydantic model. |
| LLM access | LangChain chat models (`ChatOllama`; `ChatDeepSeek` from `langchain-deepseek` for OpenAI-compatible endpoints, because `ChatOpenAI` drops reasoning output), built from the user's settings | One abstraction for chat, tools and LangGraph; the provider is a setting, not code. No separate in-house LLM port. |
| Local LLM | Ollama | Default runtime, installed or started by `/setup` (native or via Docker). Chat model: a Qwen-family tier chosen at setup from the recommended list for the detected hardware (no "qwen4" exists in Ollama's library as of 2026-11; tiers are kept in the phase 1 spec). |
| Relevance filter model | Dedicated local filter model, configurable | Developer's reference is `jaredpalmer/kev-27b` on Hugging Face; existence, licence and Ollama availability are to be verified in phase 4, with a fallback to the default chat model. |
| Prompt-injection guard | Local classifier model + rule checks | `protectai/deberta-v3-base-prompt-injection-v2` (Apache-2.0), its ONNX export at a pinned revision with pinned SHA-256 checksums, run on CPU with `onnxruntime` and `tokenizers` (no torch, transformers or optimum); text scored in 512-token windows with 64-token overlap, highest score counts, threshold `guard_threshold` (default 0.5). Plus bilingual cs/en deterministic rules, including hidden HTML text and invisible control characters. Fails closed: without a working classifier, guarded input is refused. Downloaded only after an explicit yes in chat or `/setup`, never headless. Chosen in phase 2. |
| Scraping | httpx (thread pool) + readability-style extraction (e.g. trafilatura), Playwright headless Chromium as per-site fallback | Fast for server-rendered pages, still works on JavaScript-rendered boards. Playwright is installed only when a site needs it. |
| Chat interface | Terminal chat REPL (prompt_toolkit + rich) | Slash commands (`/setup`, `/add-information`, `/scrape-jobs`, …) run tools deterministically; typing `/` opens a completion menu of commands (and, after a command, its flags) with descriptions and grey ghost text for the top match, like Claude Code's slash-command menu; an unknown `/word` gets an error with a suggestion; free text (anything not starting with `/`) goes to the agent. Argument flags keep `--`; the headless runner takes bare command names. |
| Scheduling | User crontab | `/set-crontab` installs entries that call the headless runner. |
| Containers | Docker / Docker Compose | Optional way to run Ollama and auxiliary services; the app itself runs via uv. |
| Encryption at rest | `cryptography` (Fernet) | Quarantined content is stored encrypted; the key lives in `<workspace>/.env`. |
| Remote LLM (optional) | Novita, or any OpenAI-compatible endpoint the user configures | Opt-in providers through the OpenAI-compatible LangChain chat model; the user picks the model; never the default. |

## Architecture
- One tool registry. Every capability (setup, add-information, add-web-links, scrape-jobs, list-unresolved, list-accepted, list-rejected, approve, reject, set-crontab, …) is a typed tool with Pydantic input and output. It is exposed three ways: as a slash command in chat (`/name`), as a tool the chat agent may call, and through a headless runner (`sortingcap run <command>`) used by cron. Chat-only tools (`setup`, `show-quarantine`, `release-quarantine`) are refused by the runner.
- Jobs are headless. Scraping, ingestion and letter generation never depend on the chat session; the chat starts a job and shows its result.
- The chat agent has access only to registered tools, never to a shell or arbitrary file system paths outside the workspace.
- Prompts live as files in a single `prompts/` directory tree, organised per language (`cs`, `en`) and per agent; no prompt text inline in code. Output schemas live together in one schemas package.
- LLM calls belong only in the chat agent, provider verification (`/setup`), and in extraction, filtering, research summarisation and letter steps; merging, dedupe, status changes and file layout are plain code.
- Structured LLM output always goes through the shared helper `app/providers/structured.py`: strict schema mode where the provider supports it, a bounded retry that feeds the validation error back, a bounded retry on transient errors, and a `Field(description=...)` on every field of an output schema. No direct `with_structured_output` calls elsewhere.
- Internal markers in user-facing generated files (such as the `profile-sha256` comment in the filter file) are stripped by every reader that shows the file to the user.
- Letter generation is a LangGraph reflection loop: a seller agent drafts, a sceptical company agent pokes holes, a fact-checker verifies claims of both against the user's and the company's files via the LlamaIndex index; the loop ends on acceptance or an iteration cap.
- Default letter skills ship with the app and are copied into the user's workspace, where the user edits them by hand or through the chat.

## Data
- A single user workspace directory (default under the user's home, configurable) holds everything the user owns as plain files: profile files, sources, `listing_filter_file.md`, quarantine, the injection classifier in `models/prompt-guard/<revision>/`, the workspace notes `README.md` and `AGENTS.md`, company folders with job subfolders (listing summary, original copy, company research, letters), letter skills.
- `listing_filter_file.md` is generated only: written from the profile and source texts, overwritten on each regeneration, and changed through `/add-information`, never by hand edits.
- SQLite (in the workspace) is the index for listings: status (unresolved / accepted / rejected), dedupe keys and timestamps. Files stay the source of truth for content; the index can be rebuilt from them.
- Dedupe key: normalised position title + company name + listing date.
- Files the user drops into the workspace by hand are picked up on the next relevant command.
- Postgres, pgvector and Qdrant are not used; the retrieval index is local (LlamaIndex storage in the workspace).

## Security
- Every piece of web-sourced input (scraped listings, company pages, reviews, user-supplied URLs and HTML) passes the injection guard before it is stored, indexed or shown to an agent. Flagged content is stored only encrypted in quarantine (Fernet, key in `<workspace>/.env`, metadata-only report) and reported to the user; it reaches an agent, a source file or the index only after the user explicitly releases it in chat.
- Agents that read untrusted text (filter, extraction) have no tools.
- Every guarded input path has a size cap (fetched and local alike), and every regex or parser that runs over untrusted text works in linear time: no overlapping unbounded quantifiers, no rule × element loops without an index. Each new rule gets a performance test on adversarial input.
- Scraper respects robots.txt, per-domain rate limits and identifies itself with a generic User-Agent.
- No secrets in the repo; `.env` is git-ignored, `.env.example` documents variables.

## UI & styling
- Terminal only. Czech by default, English selectable at setup; prewritten texts (setup guide, app tour) exist per language.
- Letter language follows the listing's language unless the user overrides it.
- Voice: short, dry, a bit hat-like.

## Testing policy
- Framework: pytest.
- Unit tests for all plain code (merge, dedupe, status changes, file layout, rule checks, command parsing).
- Integration tests for every tool and graph, using LangChain fake chat models, recorded HTML fixtures and a temporary workspace; no network and no real model in the default test run.
- Live-model evaluations (filter quality, letter quality, injection classifier) live in a separate, opt-in suite (`tests/live/`, pytest marker `live`, deselected by default through `addopts`; run with `uv run pytest -m live`) and are not required to pass in CI.
- Tests live in `tests/`, mirroring the `app/` package layout, files named `test_*.py`.
- Tests that drive the terminal (prompt_toolkit pipe input, keystrokes) wait on application or buffer state with a timeout, never on fixed sleeps.
- A feature is not done until its tests pass offline, ruff and the typechecker are clean, and the specs describe the code.

## Quality tooling
- Ruff for lint and format (`uv run ruff check`, `uv run ruff format`).
- Tests: `uv run pytest`.
- Typechecker: mypy (`uv run mypy app`; default mode with the pydantic plugin, `tests/` not checked).
- CI (GitHub Actions) runs ruff, mypy and the offline pytest suite.

## Constraints
- Licence: GPL-3.0; all dependencies must be GPL-compatible, and model licences must allow free redistribution of the app's use.
- Must run on a typical developer laptop with a local model; no paid service required.
- Linux first (bash, crontab); macOS best effort.

## Project structure
- Top level: `app/` (tools registry and headless runner, chat REPL and agent, providers, setup, settings and workspace, schemas, the injection guard in `guard/`, the profile intake with its LangGraph graph in `intake/`; later scraping, storage), `prompts/` (per language and agent), `defaults/` (prewritten texts and message catalogs read from the repository; later, letter skills copied to the workspace), `tests/`, `specs/`.
- No web server or web UI (FastAPI, uvicorn and htmx are out of scope); `specs/` is the design record.
