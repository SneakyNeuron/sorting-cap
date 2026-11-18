# SortingCap
<img width="200" height="200" alt="grok-image-374da938-4983-481b-9a82-a943a584ab46" src="https://github.com/user-attachments/assets/6579d771-da7d-4ac4-874b-998662354c28" />

SortingCap interviews you, reads your CV, builds a structured profile, then finds you the best fit among the live job listings. It runs locally in your terminal, Czech by default, English available.

> Status: phase 1 — chat shell, tool registry and guided model setup. Profile intake, scraping and letters follow in later phases (see `specs/roadmap.md`).

## Requirements

- Linux (macOS best effort), bash
- [uv](https://docs.astral.sh/uv/) — it installs Python 3.12 for you
- Optional: Docker (one way to run the local model), an NVIDIA GPU for faster local models

## Install

```bash
git clone https://github.com/SneakyNeuron/sorting-cap.git
cd sorting-cap
uv sync
```

## Run

```bash
uv run sortingcap            # start the chat
uv run sortingcap --version
```

On the very first start the cap asks for the language (cs/en). Then type `-setup`.

## `-setup`

A guided loop, all in the chat:

1. Language.
2. Hardware summary (RAM, NVIDIA VRAM, Docker).
3. Provider menu:
   - **Local model (Ollama)** — preselected when your hardware fits. The cap finds a running Ollama, starts a stopped one, or installs it (Docker container bound to `127.0.0.1`, or the official install script; an existing `ollama` container is reused on its published port). Every command is shown in full and runs only after your explicit yes. You then pick a Qwen model from every tier that fits your hardware (the best one is marked) and it is downloaded with a progress bar.
   - **Novita** (remote) — you enter your API key (hidden input) and pick one of Novita's function-calling models.
   - **Custom OpenAI-compatible endpoint** — any URL including its version path, e.g. `http://localhost:1234/v1` for LM Studio, vLLM or llama.cpp; optional key; model name. A `localhost`/`127.x`/`::1` URL counts as local.

   Remote providers are never preselected and always show that your prompts and data leave the machine. This includes Ollama when `ollama_base_url` points at another host; such a URL that does not answer as Ollama sends you back to the menu, and nothing is installed or started for it. A remote `http://` URL also gets a warning that prompts and any key travel unencrypted. If your hardware is too small for any local model, the local option is shown as unavailable and you can use Novita or another provider instead.
4. Verification: one chat reply and one tool call. On failure, or if you decline a step, you are back in the menu with the reason. A key typed during `-setup` is written to `<workspace>/.env` only after verification succeeds, so a mistyped key never replaces a working one; when the key's environment variable is set, it wins and `-setup` does not ask for one.

After the first successful setup the app tour is shown once.

## Commands

Type them in the chat; anything that does not start with `-` goes to the cap.

| Command | What it does |
|---|---|
| `-help [command]` | List commands, or one command's options |
| `-tour` | App overview and tour |
| `-settings [--language cs\|en] [--provider …] [--model …] [--ollama-base-url …] [--openai-base-url …]` | Show or change settings (keys are shown masked). Switching to a remote provider asks for confirmation; changing `--provider` without `--model` clears the model |
| `-setup` | Guided setup (chat only) |
| `-exit` | Quit (also Ctrl-D). Ctrl-C cancels the current line or running command |

Tab completes command names. The cap itself can only call `-help` and `-tour`; it can never run `-setup` or change settings.

## Headless runner

Every command except `setup` also runs without the chat, e.g. from cron:

```bash
uv run sortingcap run help settings
uv run sortingcap run settings --json
uv run sortingcap run settings --language en --yes
```

- `--json` prints the result as JSON on stdout.
- `--yes` answers confirmations with yes; without it every confirmation is denied. Commands that need a choice, text or a key fail.
- Exit codes: `0` success, `1` failure, `2` bad arguments or a chat-only command (`setup` — type `-setup` in the chat instead).

## Workspace

Everything the cap keeps about you is in plain files in one directory, by default `~/SortingCap`, created readable only by you (mode 0700). An existing directory keeps its mode; if others can access it, the chat warns at start and shows the `chmod 700` command. Set `SORTINGCAP_WORKSPACE` **in your shell** to move it.

| File | Contents |
|---|---|
| `settings.json` | Language, provider, model, URLs. Hand edits apply on the next command; an invalid file is reported, never overwritten |
| `.env` | Remote API keys (`NOVITA_API_KEY`, `SORTINGCAP_OPENAI_API_KEY`), mode 0600. Environment variables with the same names win. See `.env.example` — copy it to `<workspace>/.env`, not to the repository |
| `.history` | Chat input history, mode 0600 (hidden key input is never stored) |
| `logs/setup.log` | Every command `-setup` ran, with its exit code |
| `logs/ollama-serve.log` | Output of an `ollama serve` started by `-setup` (it keeps running after the app exits) |

LangSmith tracing is always off.

## Development

```bash
uv run ruff check          # lint
uv run ruff format         # format (CI runs --check)
uv run pytest              # offline test suite; network sockets are disabled
```

Design and decisions live in `specs/` (mission, tech stack, roadmap, and one folder per feature).

## Licence

GPL-3.0
