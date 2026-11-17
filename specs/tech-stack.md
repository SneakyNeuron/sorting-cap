# Tech stack

## Runtime & language
- Python 3.12, managed by uv (project, virtualenv, Python install and lockfile). The app is started with `uv run`.
- Dependencies are declared in `pyproject.toml` and pinned exactly in `uv.lock`. Exact versions are chosen and verified when the phase that introduces a dependency is planned.

## Frameworks & libraries
| Concern | Choice | Why |
|---|---|---|
| Agents & orchestration | LangGraph on LangChain | Stateful graphs for intake, scraping pipeline and the reflection loop; LangChain tool and model abstractions. |
| Document ingestion & retrieval | LlamaIndex | Loading .txt/.pdf/.html/web sources and the local index the fact-checker queries. |
| Schemas | Pydantic v2 | Every structured LLM output, every tool input/output and every stored record is a Pydantic model. |
| LLM access | LangChain chat models (Ollama and OpenAI-compatible integrations), built from the user's settings | One abstraction for chat, tools and LangGraph; the provider is a setting, not code. No separate in-house LLM port. |
| Local LLM | Ollama | Default runtime, installed or started by `-setup` (native or via Docker). Default chat model: Qwen family (the "qwen4" default named by the developer is to be verified against Ollama's catalog in phase 1). |
| Relevance filter model | Dedicated local filter model, configurable | Developer's reference is `jaredpalmer/kev-27b` on Hugging Face; existence, licence and Ollama availability are to be verified in phase 3, with a fallback to the default chat model. |
| Prompt-injection guard | Local classifier model + rule checks | A small local injection classifier (candidates: Llama Prompt Guard 2, ProtectAI deberta prompt-injection; chosen and verified in phase 2) plus deterministic rules. |
| Scraping | httpx (thread pool) + readability-style extraction (e.g. trafilatura), Playwright headless Chromium as per-site fallback | Fast for server-rendered pages, still works on JavaScript-rendered boards. Playwright is installed only when a site needs it. |
| Chat interface | Terminal chat REPL | Dash-commands (`-setup`, `-add-information`, `-scrape-jobs`, …) run tools deterministically; free text goes to the agent. |
| Scheduling | User crontab | `-set-crontab` installs entries that call the headless runner. |
| Containers | Docker / Docker Compose | Optional way to run Ollama and auxiliary services; the app itself runs via uv. |
| Remote LLM (optional) | Novita via OpenAI-compatible API | Opt-in provider through the OpenAI-compatible LangChain chat model; never the default. |

## Architecture
- One tool registry. Every capability (setup, add-information, add-web-links, scrape-jobs, list-unresolved, list-accepted, list-rejected, approve, reject, set-crontab, …) is a typed tool with Pydantic input and output. It is exposed three ways: as a dash-command in chat, as a tool the chat agent may call, and through a headless runner (`sortingcap run <command>`) used by cron.
- Jobs are headless. Scraping, ingestion and letter generation never depend on the chat session; the chat starts a job and shows its result.
- The chat agent has access only to registered tools, never to a shell or arbitrary file system paths outside the workspace.
- Prompts live as files in a single `prompts/` directory tree, organised per language (`cs`, `en`) and per agent; no prompt text inline in code. Output schemas live together in one schemas package.
- LLM calls belong only in extraction, filtering, research summarisation and letter steps; merging, dedupe, status changes and file layout are plain code.
- Letter generation is a LangGraph reflection loop: a seller agent drafts, a sceptical company agent pokes holes, a fact-checker verifies claims of both against the user's and the company's files via the LlamaIndex index; the loop ends on acceptance or an iteration cap.
- Default letter skills ship with the app and are copied into the user's workspace, where the user edits them by hand or through the chat.

## Data
- A single user workspace directory (default under the user's home, configurable) holds everything the user owns as plain files: profile files, sources, `listing_filter_file.md`, quarantine, company folders with job subfolders (listing summary, original copy, company research, letters), letter skills.
- SQLite (in the workspace) is the index for listings: status (unresolved / accepted / rejected), dedupe keys and timestamps. Files stay the source of truth for content; the index can be rebuilt from them.
- Dedupe key: normalised position title + company name + listing date.
- Files the user drops into the workspace by hand are picked up on the next relevant command.
- Postgres, pgvector and Qdrant are not used; the retrieval index is local (LlamaIndex storage in the workspace).

## Security
- Every piece of web-sourced input (scraped listings, company pages, reviews, user-supplied URLs and HTML) passes the injection guard before it is stored, indexed or shown to an agent. Flagged content goes to quarantine and is reported to the user; it is never forwarded.
- Agents that read untrusted text (filter, extraction) have no tools.
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
- Live-model evaluations (filter quality, letter quality, injection classifier) live in a separate, opt-in suite and are not required to pass in CI.
- Tests live in `tests/`, mirroring the `app/` package layout, files named `test_*.py`.
- A feature is not done until its tests pass offline, ruff is clean, and the specs describe the code.

## Quality tooling
- Ruff for lint and format (`uv run ruff check`, `uv run ruff format`).
- Tests: `uv run pytest`.
- CI (GitHub Actions) runs ruff and the offline pytest suite; added in phase 1.

## Constraints
- Licence: GPL-3.0; all dependencies must be GPL-compatible, and model licences must allow free redistribution of the app's use.
- Must run on a typical developer laptop with a local model; no paid service required.
- Linux first (bash, crontab); macOS best effort.

## Project structure
- Top level: `app/` (tools registry, chat, runner, graphs, scraping, guard, storage, schemas, llm settings), `prompts/` (per language and agent), `defaults/` (letter skills and prewritten texts shipped to the workspace), `tests/`, `specs/`.
- No web server or web UI (FastAPI, uvicorn and htmx are out of scope); `specs/` is the design record.
