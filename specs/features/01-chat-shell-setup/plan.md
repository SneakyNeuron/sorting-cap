# Phase 01: Chat shell, tool registry and setup — Plan

## Context
Roadmap Phase 1. The repository holds only the constitution; there is no code. This phase lays the foundation every later phase builds on: the uv project, the single tool registry (dash-commands, agent tools and the headless runner), the terminal chat, the user workspace and settings, and a guided `-setup` that leaves the user with a working, verified LLM provider (local Ollama by default). Success is a user who clones the repo, runs `uv sync` and `uv run sortingcap`, types `-setup`, and ends with a verified model and the app tour, without editing config by hand.

## Approach
- **One registry, three faces.** A tool is a typed object: name, a message key for its description, Pydantic input model, Pydantic output model, an `agent_callable` flag, a `chat_only` flag and a handler that receives a `ToolContext` (settings, workspace, texts, output console, and the prompts `confirm()`, `choose()`, `text()`, `secret()`). An argparse parser is generated from the registry, including positional fields. The same parser serves `sortingcap run <command> ...` and dash-commands typed in chat (split with `shlex`), so the CLI, cron and chat behave identically. The chat agent sees only tools marked `agent_callable`; the headless runner refuses tools marked `chat_only` (Phase 1: `setup`, which needs choices, text and hidden-key input).
- **Texts first.** Help texts for tools and flags are message keys resolved per language from `defaults/texts/<lang>/messages.json`, so the text loader comes before the registry. Prompts live under `prompts/<lang>/<agent>/`; prewritten user texts (overview, tour) under `defaults/texts/<lang>/`. Czech is the default and English is complete.
- **Providers.** A small provider registry (`app/providers/`). Each provider knows whether it is available on this machine, runs its own setup steps, verifies itself and builds a LangChain chat model from settings. Phase 1 ships `ollama` (local, default; `ChatOllama`), `novita` (remote, opt-in) and `openai_compatible` (any OpenAI-compatible endpoint the user points to: URL, optional key, model); both OpenAI-compatible providers use `ChatDeepSeek` from `langchain-deepseek` with a custom `api_base`, because `ChatOpenAI` drops `reasoning_content`. Later providers plug in without changing `-setup`. The registry and provider names exist from Group 4 so settings can validate against them; each provider's chat model and setup steps are filled in later groups.
- **Agent.** LangChain 1.x `create_agent` with the chat model from the configured provider. The system prompt is loaded from `prompts/<lang>/chat/system.md`. When `-settings` (or a hand edit) changes the provider, model or language, the agent and chat model are rebuilt before the next message and the conversation history is kept. Tests use a small fake chat model that supports `bind_tools`.
- **REPL.** prompt_toolkit for input (history file in the workspace, tab completion of dash-commands generated from the registry, multi-line paste), rich for output.
- **Workspace and settings.** The default workspace is `~/SortingCap`, overridden by `SORTINGCAP_WORKSPACE`. `settings.json` holds the non-secret settings as a Pydantic model and is re-read on every command, so hand edits take effect. Remote API keys live in `<workspace>/.env` with mode 0600; an environment variable overrides them.
- **`-setup` as a selection loop.** A user-only tool: language, hardware summary, then a provider menu (local Ollama preselected when the hardware fits, Novita, any OpenAI-compatible endpoint, and later more). For local Ollama the user sees every recommended Qwen tier from T5 with the one best for the detected hardware marked; remote providers have no default model, the user picks one. The chosen provider's setup runs; if it fails or the user declines a step, the user returns to the menu with the reason and can pick another route or quit. If no local model tier fits the hardware, the local option is shown as unavailable with a message saying the hardware is not sufficient and that Novita or other providers can run the agents instead. On success the provider is verified (a chat reply plus a tool call), settings are saved and the tour is shown on first success. The Ollama install work runs commands with root rights, so it gets its own group.
- **Layout.** `uv_build` backend with `app/` as the import package (distribution `sortingcap`). `prompts/` and `defaults/` stay at the repository root and are found relative to the package (`uv sync` installs the project in editable mode); PyPI packaging is out of scope.

## What stays unchanged
- `specs/mission.md` and `specs/tech-stack.md`, unless a decision below forces an update, which is then made in the same commit.
- `LICENSE`.
- No web server, web UI or database in this phase.

## Group 1: uv scaffold and CI
- [x] `pyproject.toml` for distribution `sortingcap` (Python 3.12, `requires-python = ">=3.12,<3.13"`), `uv_build` backend configured with module `app` at the repository root, console script `sortingcap = "app.cli:main"`, runtime and dev dependency groups (T1); `uv.lock` committed
- [x] `.gitignore` (venv, caches, `.env`, build output) and `.env.example` documenting `NOVITA_API_KEY` and `SORTINGCAP_OPENAI_API_KEY`; its header comment says the file is copied to `<workspace>/.env` (not the repository root) and that `SORTINGCAP_WORKSPACE` must be set in the shell, not in that file
- [x] Ruff config in `pyproject.toml` (lint + format; rule set: `E`, `F`, `I`, `B`, `UP`, `SIM`); pytest config enabling pytest-socket so tests run with network sockets disabled (`--disable-socket --allow-unix-socket`: asyncio's event loop needs a Unix `socketpair()`), with `--import-mode=importlib` so test files that mirror `app/` may share basenames
- [x] `app/` package with `app/cli.py` whose `main()` supports `--version`, and `app/paths.py` resolving the repository's `prompts/` and `defaults/` directories
- [x] `.github/workflows/ci.yml`: install uv, `uv sync --locked`, `uv run ruff check`, `uv run ruff format --check`, `uv run pytest`
- [x] Tests: `sortingcap --version` prints the package version and exits 0 (`tests/test_cli.py`); `app/paths.py` finds both directories (`tests/test_paths.py`); `tests/test_project.py` checks `uv.lock` against the T1 ranges, the `uv_build` backend and the absence of `python-dotenv`

## Group 2: Texts, prompts and message catalog
- [x] `app/texts.py`: loaders for prompts (`prompts/<lang>/<agent>/*.md`), prewritten texts and messages (`defaults/texts/<lang>/messages.json`) by language; a missing key or file fails loudly; the only module that reads `prompts/`
- [x] `prompts/cs/chat/system.md` and `prompts/en/chat/system.md` (the cap's voice: short, dry, a bit hat-like; may call only the listed tools; points the user to `-setup` for setup and to `-help`)
- [x] `defaults/texts/{cs,en}/overview.md`, `tour.md` and `messages.json` (UI strings plus tool and flag help keys, filled as tools are added in later groups)
- [x] Tests: cs and en contain the same files and the same message keys; loader errors on a missing key or file; every file under `prompts/` is reachable through `app/texts.py`

## Group 3: Tool registry, generated parser and headless runner
- [x] `app/schemas/`: home of the tool input/output Pydantic models
- [x] `app/tools/registry.py`: `Tool` definition (name, description key, input/output Pydantic models, `agent_callable`, `chat_only`, handler), `ToolContext` (with `confirm()`, `choose()`, `text()`, `secret()`), and a registry that rejects duplicate names and tools whose message keys are missing in either language
- [x] Parser generation: one argparse subparser per tool; fields marked positional become positional arguments, other fields become flags (type, default, required, help from the field's message key in the current language); bool fields become `--flag/--no-flag`; validation errors come from Pydantic and are shown as a short message
- [x] `app/tools/headless.py`, `sortingcap run <command> [args] [--json] [--yes]`: runs the tool headless; a `chat_only` tool is refused with a message to use it in chat and exit code 2; renders the output with rich, or prints the output model as JSON with `--json`; exit code 0 on success, 2 on bad arguments, 1 on tool failure or an incomplete run
- [x] Non-interactive prompts: in the runner, `ToolContext.confirm()` returns false unless `--yes` was given; `choose()`, `text()` and `secret()` fail the run with a message naming the missing input (exit 1)
- [x] `-help` tool (`app/tools/help.py`, agent-callable): lists commands (including REPL built-ins such as `-exit`) with descriptions in the current language; `-help <command>` (positional) shows that command's arguments
- [x] `-tour` tool (`app/tools/tour.py`, agent-callable): renders the overview and the tour as Markdown
- [x] Tests: registry duplicates and missing keys, argument generation for positional/str/int/bool/enum/optional fields, runner exit codes, `--json` output, `--yes` handling, a `chat_only` stub tool refused by the runner with exit 2, `choose()`/`text()`/`secret()` failing headless, help listing and `-help <command>` in cs and en, `-tour` in cs and en

## Group 4: Workspace and settings
- [x] `app/workspace.py`: resolve the workspace (`SORTINGCAP_WORKSPACE` or `~/SortingCap`), create it on first use
- [x] `app/providers/base.py`: `Provider` interface (name, label key, `is_remote(settings)`, `availability(hardware)`, `run_setup(ctx)`, `verify(ctx)`, `chat_model(settings, secrets)`) and the provider registry, with `ollama`, `novita` and `openai_compatible` registered by name and label; the methods are filled in Groups 6–10. `is_remote` is false for `ollama`, true for `novita`, and for `openai_compatible` true unless `openai_base_url`'s host is loopback (`localhost`, `127.0.0.0/8`, `::1`), via one shared helper
- [x] `app/settings.py`: `Settings` Pydantic model (`language: cs|en` default `cs`, `provider` (a registered provider name or null), `model`, `ollama_base_url` default `http://localhost:11434`, `openai_base_url` (custom endpoint), `setup_completed_at`, `tour_shown`); read from `settings.json` on every command; an invalid file produces a clear error naming the file and field, and the file is never overwritten silently
- [x] `app/credentials.py` (not `secrets.py`, which would share the stdlib module's name): read `NOVITA_API_KEY` and `SORTINGCAP_OPENAI_API_KEY` from the environment, falling back to `<workspace>/.env` (small built-in `KEY=value` parser); writing a key creates or updates `.env` with mode 0600
- [x] `app/tools/settings.py`, `-settings` tool (user-only): with no arguments shows the current settings (keys masked); `--language`, `--provider`, `--model`, `--ollama-base-url`, `--openai-base-url` change them; a change that makes the configured provider remote (R10: `--provider novita`, or `openai_compatible` with a non-loopback `openai_base_url`) first shows the data-leaves-the-machine notice and asks `confirm()`; declining changes nothing. `-settings` is a plain edit: it neither asks for keys nor verifies the provider (that is `-setup`'s job); changing `--provider` without `--model` clears `model` and says to set `--model` or run `-setup`
- [x] Tests: workspace resolution and creation (temporary home), uncreatable path error, settings defaults, hand-edited file picked up, provider change without `--model` clears the model, invalid file error, `.env` written with 0600 and env var precedence, `-settings` show and set, provider names validated against the registry, the loopback helper, the remote notice on `-settings` with confirm and decline (nothing changed), and denied headless without `--yes`

## Group 5: Chat REPL with dash-commands
- [x] `app/repl.py`: `sortingcap` with no subcommand starts the REPL: greeting in the configured language, prompt_toolkit session with history in `<workspace>/.history`, completion of dash-commands and REPL built-ins generated from the registry, multi-line paste
- [x] A line starting with `-` is parsed by the generated parser and runs the tool in the chat `ToolContext` (interactive `confirm()`); errors print a short message and keep the REPL running
- [x] `-exit` (REPL built-in, not a registered tool) and Ctrl-D quit cleanly; Ctrl-C cancels the current line or the running tool without quitting
- [x] First start: when no language has been chosen yet (no `settings.json`), ask for the language with a bilingual prompt (cs/en) and save it before the greeting
- [x] While `setup_completed_at` is null, the greeting suggests `-setup`
- [x] Tests: drive the REPL through prompt_toolkit's test input pipe; dash-command dispatch, the same tool run as a chat line and as `sortingcap run` yields the same input model and output (V2), unknown command message, bad-flag message, a failing tool keeps the REPL running, `-exit`, Ctrl-D, Ctrl-C cancels without quitting, multi-line paste arrives as one input, history written to `<workspace>/.history`, completion candidates, first-start language prompt saved before the greeting, setup hint shown until setup completes

## Group 6: Providers and chat agent
- [x] `chat_model` for the three providers registered in Group 4: `ChatOllama` at `ollama_base_url` with `reasoning=True`; `ChatDeepSeek` (`langchain-deepseek`) with `api_base` set to Novita's base URL and the Novita key; `ChatDeepSeek` with `api_base` set to `openai_base_url` and the optional custom key (placeholder `not-needed` when empty); `api_key` and `api_base` are always passed explicitly so `DEEPSEEK_API_KEY`/`DEEPSEEK_API_BASE` from the environment are never used; their setup steps follow in Groups 8–10
- [x] `app/agent.py`: `create_agent` with the system prompt for the current language and LangChain tools wrapped from the `agent_callable` registry tools only; conversation memory for the REPL session is a message list owned by the REPL (no checkpointer) and passed in full on every turn, so a rebuilt agent keeps it; when the re-read settings show a changed provider, model or language, the chat model and agent (with that language's system prompt) are rebuilt before the next message and the history is kept
- [x] Free text in the REPL goes to the agent; reasoning returned by the model is shown in dim text above the answer, the answer is rendered as Markdown; a spinner shows while the model works
- [x] At startup, in `app/cli.py` before any `langchain`/`langsmith` import, force LangSmith tracing off in the app's process (T15)
- [x] Without a configured provider and model, free text gets a "run `-setup` first" message instead of an agent call
- [x] Model or connection errors print a short message naming the provider and suggesting `-setup`; the REPL stays alive
- [x] Tests (with a fake chat model supporting `bind_tools`): text reply; reasoning shown dim and separate from the answer, both from a fake model and from a faked OpenAI-compatible HTTP response whose `reasoning_content` passes through `ChatDeepSeek`; `ChatOllama` built with `reasoning=True`; agent rebuilt with history kept after a provider, model or language change; tracing off even when `LANGSMITH_TRACING=true` is set (checked in a fresh subprocess through `langsmith.utils.tracing_is_enabled()`); a tool call to `help` runs the tool; the agent's tool list excludes a stub user-only tool and `settings`; chat model built per provider; a set `DEEPSEEK_API_KEY` never reaches a custom endpoint; the no-model path; error path

## Group 7: `-setup` selection loop, hardware detection and command runner
- [ ] `app/setup/runner.py`: the one function that runs external commands (the only `subprocess` user); logs each command line and its exit code to `<workspace>/logs/setup.log`; no command line ever contains a key; replaced by a fake in tests. Three modes: captured (output returned), interactive (inherits the terminal so a sudo prompt is visible; any spinner is stopped first) and detached (new session, output to `<workspace>/logs/<name>.log`, survives the REPL; the log records the PID instead of an exit code)
- [ ] `app/setup/hardware.py`: total RAM from `/proc/meminfo`, NVIDIA VRAM via `nvidia-smi` when present, Docker availability and Docker GPU support (NVIDIA container toolkit)
- [ ] `app/setup/flow.py`, `-setup` tool (user-only, `chat_only`; prompts use `ToolContext.choose()`, `text()`, `secret()` and `confirm()`): language step first (cs default, en) and saved immediately; hardware summary; provider menu built from the provider registry with each provider's availability (local preselected when available; a remote provider is never preselected); the chosen provider's `run_setup` then `verify`; on failure or a declined step return to the menu with the reason; the user can quit the loop (setup stays incomplete)
- [ ] When no local tier fits the hardware, the local option is marked unavailable with the message that the hardware is not sufficient and Novita or other providers can run the agents instead
- [ ] On success save `provider`, `model` and `setup_completed_at`; show the tour if `tour_shown` is false, then set it
- [ ] Re-running `-setup` on a configured machine shows the current provider and model and enters the same menu
- [ ] `sortingcap run setup` is refused by the runner (chat-only, exit 2), with or without `--yes`
- [ ] Tests (with fake providers): language saved immediately; menu reflects availability; nothing preselected when local is unavailable; the no-fit message; failure returns to the menu; quit leaves setup incomplete; success saves settings; tour shown only once; re-run path; `sortingcap run setup` refused with exit 2; command runner logs and masks nothing secret

## Group 8: Local provider part 1: Ollama detection, start and install
- [ ] `app/providers/ollama/server.py`: detect a running Ollama (`GET /api/version` at `ollama_base_url`), something else answering on the port, an installed but stopped binary, an existing but stopped `ollama` Docker container, or nothing
- [ ] Start: stopped binary → offer `ollama serve` detached (runner detached mode), or `sudo systemctl start ollama` when a systemd unit exists; stopped container → offer `docker start ollama`
- [ ] Install, each after an explicit yes that shows the exact command (T6): (a) Docker: run the `ollama/ollama` container with a named volume, the port bound to `127.0.0.1` only, and `--gpus=all` when the GPU toolkit is present; (b) native: run the official install script in the runner's interactive mode (it uses sudo; the user's own sudo prompt is shown in the terminal). The default offered is Docker when available, native otherwise
- [ ] Port taken by something that is not Ollama → offer the next free port for the Docker container and save it as `ollama_base_url`
- [ ] After install or start, poll until the server answers or a timeout passes, then report
- [ ] All commands go through `app/setup/runner.py`
- [ ] Tests: each detection outcome; start (binary, systemd, container), Docker (with and without GPU, other port, port bound to `127.0.0.1`) and native paths with faked commands and HTTP; "no" at each confirmation returns to the menu; `docker` permission error message; timeout path

## Group 9: Local provider part 2: model tiers, pull and verification
- [ ] `app/providers/ollama/tiers.py`: model tiers from T5: `availability` reports the tiers that fit the detected RAM/VRAM (none → unavailable); `-setup` lists every fitting tier with the best one (T5 selection rule: GPU-fit first, lowest `#`) marked and preselected, and the user picks
- [ ] Pull with a rich progress bar from the `POST /api/pull` stream; skip when the model is already in `GET /api/tags`
- [ ] `verify`: a short chat answer plus one tool-call round trip through the agent with the real model; a model without tool calling fails with that reason and the user may pick another tier
- [ ] Tests: tier selection per hardware profile (including none fitting); pull skip and progress; interrupted pull reported; verification pass/fail with a fake model

## Group 10: Remote providers: Novita and any OpenAI-compatible endpoint
- [ ] `app/providers/remote.py`, shared remote step: explain that prompts and data leave the machine (declining returns to the menu); keys are entered with hidden input that never reaches the REPL history, and stored in `<workspace>/.env` (0600)
- [ ] `openai_compatible` with a loopback host (`localhost`, `127.0.0.0/8`, `::1`) counts as local: no data notice
- [ ] `app/providers/novita.py`, `novita` (always available): ask for the key; fetch Novita's model list (`GET <base>/models`, base `https://api.novita.ai/openai/v1`), show the models that support function calling and let the user pick one (or type an id); no default model
- [ ] `app/providers/openai_compatible.py`, `openai_compatible` (always available): ask for the base URL including its version path, OpenAI convention (e.g. `http://localhost:1234/v1`; http/https, validated, trailing slash dropped), an optional key and the model name; save `openai_base_url` and `model`; the key goes to `.env` as `SORTINGCAP_OPENAI_API_KEY`
- [ ] `verify` for both: one chat reply plus one tool call; on failure (bad key, bad URL, network, model without tool calling) return to the menu with the reason
- [ ] Tests (faked HTTP; the Novita `/v1/models` fixture copies the live response shape, including the `features` list with `serverless`, `function-calling`, `reasoning`, `structured-outputs`): Novita model list filtered to function-calling models and the user's pick saved; custom endpoint URL validation, optional key, model saved, loopback hosts skip the notice and LAN hosts do not; keys absent from `.history`; keys stored with 0600 and never in `settings.json`, logs or output; env var precedence; verification pass/fail for both; declining the data notice returns to the menu

## Group 11: README and spec sync
- [ ] README: requirements (Linux, uv, optional Docker), install (`git clone`, `uv sync`), run (`uv run sortingcap`), `-setup` and its provider menu, command list, headless runner (and that `setup` is chat-only), workspace location and files, where `.env` goes (`<workspace>/.env`, with `SORTINGCAP_WORKSPACE` set in the shell), development (ruff, pytest)
- [ ] Specs match the code: update `tech-stack.md` "Project structure" if the layout differs, and record any decision changes in `requirements.md`
- [ ] Tests: full offline suite and ruff clean

## Edge cases
- `settings.json` hand-edited into invalid JSON or an invalid value → the command fails with a message naming the file and field; nothing is overwritten.
- `SORTINGCAP_WORKSPACE` points to a path that cannot be created → clear error at start, exit 1.
- `-settings --provider` without `--model` → `model` is cleared and the user is told to set `--model` or run `-setup`.
- `DEEPSEEK_API_KEY` is set in the user's environment → never sent anywhere; keys are always passed explicitly.
- Ollama runs on a non-default URL → `ollama_base_url` in settings is used everywhere.
- Port 11434 is used by something that is not Ollama → detection says so; Docker is offered on the next free port, saved as `ollama_base_url`.
- A stopped `ollama` container already exists → offer `docker start ollama` instead of a new `docker run`.
- Docker is present but the user is not in the `docker` group → the step fails with a message suggesting the native path or adding the user to the group, and the user returns to the menu.
- No NVIDIA GPU → tiers use RAM only, and Docker runs without `--gpus`.
- No local tier fits → local is shown as unavailable with the "hardware not sufficient, use Novita or another provider" message.
- Novita's model list cannot be fetched → the user can type a model id; verification decides.
- The custom endpoint has no `<base>/models` or rejects tool calls → verification fails with that reason and the user returns to the menu.
- The pull is interrupted (Ctrl-C or network) → reported, back to the menu; re-running `-setup` resumes or restarts the pull.
- The model does not support tool calling → verification fails with that reason; the user can pick another tier or provider.
- `sortingcap run setup` (any flags) → refused as chat-only with exit 2 and a hint to type `-setup` in chat.
- `-settings` switches to a remote provider headless without `--yes` → the notice's confirmation is denied, nothing changes, exit 1.
- The user quits the menu → setup stays incomplete and the REPL keeps suggesting `-setup`.
- The agent is asked to "set me up" → it cannot call `setup` and tells the user to type `-setup`.
