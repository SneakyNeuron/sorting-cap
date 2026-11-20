# SortingCap
<img width="200" height="200" alt="grok-image-374da938-4983-481b-9a82-a943a584ab46" src="https://github.com/user-attachments/assets/6579d771-da7d-4ac4-874b-998662354c28" />

SortingCap interviews you, reads your CV, builds a structured profile, then finds you the best fit among the live job listings. It runs locally in your terminal, Czech by default, English available.

> Status: phase 3 — chat shell, guided model setup, conversational profile intake with a per-item checklist status, the generated filter file and the prompt-injection guard with an encrypted quarantine. Scraping and letters follow in later phases (see `specs/roadmap.md`).

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

On the very first start the cap asks for the language (cs/en). Then type `/setup`.

## `/setup`

A guided loop, all in the chat:

1. Language.
2. Hardware summary (RAM, NVIDIA VRAM, Docker).
3. Provider menu:
   - **Local model (Ollama)** — preselected when your hardware fits. The cap finds a running Ollama, starts a stopped one, or installs it (Docker container bound to `127.0.0.1`, or the official install script; an existing `ollama` container is reused on its published port). Every command is shown in full and runs only after your explicit yes. You then pick a Qwen model from every tier that fits your hardware (the best one is marked) and it is downloaded with a progress bar.
   - **Novita** (remote) — you enter your API key (hidden input) and pick one of Novita's function-calling models.
   - **Custom OpenAI-compatible endpoint** — any URL including its version path, e.g. `http://localhost:1234/v1` for LM Studio, vLLM or llama.cpp; optional key; model name. A `localhost`/`127.x`/`::1` URL counts as local.

   Remote providers are never preselected and always show that your prompts and data leave the machine. This includes Ollama when `ollama_base_url` points at another host; such a URL that does not answer as Ollama sends you back to the menu, and nothing is installed or started for it. A remote `http://` URL also gets a warning that prompts and any key travel unencrypted. If your hardware is too small for any local model, the local option is shown as unavailable and you can use Novita or another provider instead.
4. Verification: one chat reply and one tool call. On failure, or if you decline a step, you are back in the menu with the reason. A key typed during `/setup` is written to `<workspace>/.env` only after verification succeeds, so a mistyped key never replaces a working one; when the key's environment variable is set, it wins and `/setup` does not ask for one.
5. Injection classifier: after a successful verification the cap offers to download the local prompt-injection classifier (about 0.7 GB, see [The guard](#the-guard)). Saying no keeps setup successful; the offer comes back in chat the first time a URL or `.html` file needs it.

After the first successful setup the app tour is shown once.

## Commands

Type them in the chat; anything that does not start with `/` goes to the cap. A `/word` that is not a command is not sent to the cap: you get an error, with a suggestion when a command name is close. Argument flags keep two dashes (`--source`, `--json`, …).

| Command | What it does |
|---|---|
| `/help [command]` | List commands, or one command's options |
| `/tour` | App overview and tour |
| `/settings [--language cs\|en] [--provider …] [--model …] [--ollama-base-url …] [--openai-base-url …] [--guard-threshold …]` | Show or change settings (keys are shown masked). Switching to a remote provider asks for confirmation; changing `--provider` without `--model` clears the model. `--guard-threshold` (between 0 and 1, default 0.5) is the classifier score at which web content is quarantined |
| `/setup` | Guided setup (chat only) |
| `/add-information [--source <path\|url> …]` | A conversation about you: facts, file paths and links in any message; the cap asks about what is missing or only partly known, then updates the profile, its checklist status and the filter file. With `--source` (repeatable) there is no conversation |
| `/show-profile` | Your profile items with their state (missing, partly known with what is still missing, known) and where each was learned, sources (CV flagged), the filter file and where each file lives |
| `/show-quarantine [<id>]` | List quarantined content, or decrypt one item and print it as plain text (chat only) |
| `/release-quarantine <id>` | Show one quarantined item and, after your explicit yes, add it as a source (chat only) |
| `/exit` | Quit (also Ctrl-D). Ctrl-C cancels the current line or running command |

Typing `/` opens a menu of commands with their descriptions, like Claude Code's slash-command menu; it filters as you type, and the top match is also shown as grey ghost text. ↑/↓ move the highlight, Tab (or → at the end of the line) takes the highlighted command, Enter runs it, Esc closes the menu. After a command and a space the same menu offers its flags (flags you already gave are left out unless they repeat); `/help <partial>` completes command names. Free text gets no menu. `/help` lists every command on one line, shortened to the terminal width.

The cap itself can only call `/help`, `/tour` and `/show-profile`; it can never run `/setup`, change settings, add information or touch the quarantine.

## `/add-information`

In chat it runs one intake round, a free conversation with the cap's intake agent:

- **The cap opens** with what it still needs, based on the checklist status (see below). There is no form: no sources prompt, no introduction prompt, no prewritten questions.
- **You answer in your own words.** Any message may mix facts about you with file paths and links: `http(s)://` URLs, `www.` hosts (read as `https://`), and paths ending in `.txt`, `.md`, `.pdf`, `.html` or `.htm` (absolute, `~/…`, `./…`, `../…`, or relative; a path with spaces in quotes). A relative path without `./`, such as `README.md` or `docs/cv.pdf`, counts only when that file exists in the current directory. Supported: `.txt`, `.md` (UTF-8, with a cp1250 / latin-1 fallback for older Czech files), `.pdf` (text only, no OCR; local `.txt`, `.md` and `.pdf` files at most 20 MiB), `.html`/`.htm` and web pages (HTML only, at most 5 MiB, 20 s timeout, 5 redirects). Each file or link is added and read at once, with one line per source in the chat ("added cv.pdf; filled Key skills, Education"); at most 10 per message. A path you wrote explicitly that does not exist is reported as not found, and the cap asks where the file is.
- **The cap asks** about missing must-know items first (target roles, work experience, key skills, education, languages with level), then about holes in partly known items (a salary without a currency, say), then about missing nice-to-have items (CV, salary, contract type HPP / IČO / DPP / DPČ, locations, work mode, earliest start, industries or companies to prefer or avoid).
- **Links and files it did not spot**: when you write `firma.cz/kariera` or "my CV is somewhere in Downloads", the cap can add the link, or search for the file by name (in the folder you named, or the current directory, `~/Downloads`, `~/Documents`, `~/Desktop` and `~`, three levels deep, at most 20 results). It may only add what you typed this round; a file it found, or a file that matches what you typed only by its name, is read only after you confirm the full path (default no). It searches only those default folders or a folder you typed this round, never a hidden (dot) folder inside them. A link you typed with `https://`, as `www.` or without a scheme is opened over `https` only; plain `http://` only when you typed it.
- **Ending**: the cap ends the round itself when nothing worth asking is left or you want to stop. Typing exactly `done` (cs also `hotovo`) or pressing Ctrl-D ends and saves at once, without asking the model. Ctrl-C cancels the round: sources already copied stay (and are read next round), `profile.md`, `checklist.json` and the filter file stay as they were.
- **When the model fails** on a turn, you get a short error and can write again; nothing said so far is lost. After 3 failed turns in a row the round ends and is saved, with a note in the summary.

What you say is recorded as a direct answer: it replaces its item, or is added to it as a new paragraph, and settles conflicts on that item. Sources only fill empty items: when they say something different from an item you already have, the item is left alone and the difference is listed as a conflict for you to resolve. The intake agent never sees the text of a file or web page; a separate model without tools reads each source and the agent only learns which items it filled, added to or conflicts with. The conversation itself is not stored anywhere: the next round starts from `profile.md`, `checklist.json` and the source manifest.

The round ends with a summary: items filled or changed, items still missing, partly known items with what is still missing, conflicts, every source's outcome (added, skipped as a duplicate, quarantined with its id and reason, refused, error), notes and the path of every profile file.

Headless, the round runs without a conversation:

```bash
uv run sortingcap run add-information --source ~/cv.pdf --source https://example.com/me
uv run sortingcap run add-information          # only dropped-in files and unfinished sources
```

`/add-information --source …` in chat does the same. Without a configured model it stops before copying anything, with a hint to run `/setup` (exit 1). Exit 1 also when a `--source` you named fails (not found, unreadable, unsupported, fetch error, empty text) or a URL / `.html` source is refused because the classifier is missing, and when the filter file could not be regenerated (a model error, or Ctrl-C during that step; `profile.md` is already saved); the other sources are still processed and the summary is printed. Errors on dropped-in files are only reported.

### Profile files

| Path | Contents |
|---|---|
| `profile/profile.md` | One `## <title> <!-- item:<id> -->` section per checklist item. Edit it by hand any time; the id comment is what the cap keys on, so keep it (the title may change). Sections without an id comment are kept verbatim. The CV section is written by the cap from the sources flagged as CV |
| `profile/sources/<slug>/` | One folder per source: `original.<ext>` (for URLs the fetched `original.html` plus `source.url` with the final URL) and the extracted `text.md` |
| `profile/checklist.json` | The checklist status of each item: `missing`, `partial` (with a note on what is still missing) or `known`, where it was learned (`conversation`, `file:<slug>`, `web:<slug>`, `hand_edit`, `profile`) and when. Reconciled with `profile.md` on every read, so an item you empty by hand counts as missing and one you fill by hand as known; an invalid file is rebuilt from `profile.md` with a note. Written at the end of a round, also by headless rounds |
| `profile/sources/manifest.json` | Every source: origin, SHA-256, type, CV flag, whether it was extracted, time. Adding the same content again (even under another name) is skipped |
| `listing_filter_file.md` | Generated from the profile and the source texts: what you are looking for, must-match and nice-to-match criteria, exclusions, keywords in Czech and English. **Generated only**: overwritten every time, change it through `/add-information`. A `<!-- profile-sha256: … -->` marker records the `profile.md` it was built from; it is regenerated when that hash differs (e.g. after a hand edit of `profile.md`), when it is missing or unreadable. A failed generation leaves the previous file in place |

Files you drop into `profile/sources/` yourself (any supported type) are picked up by the next `/add-information`, chat or headless. A source whose extraction did not finish (cancelled round, model error) is extracted again from its stored `text.md` next round, without fetching it again. Long texts are cut at 12 000 characters, and the summary says so.

The model's structured replies (extraction, filter file) are checked against a schema. Ollama models get schema-constrained output, OpenAI-compatible providers (Novita, custom endpoints) function calling. A reply that does not match is retried up to twice with the error fed back (`prompts/<lang>/structured/retry.md`); connection errors, timeouts and HTTP 429 are retried up to three attempts in total.

## The guard

Everything from the web — URLs and `.html`/`.htm` files you supply — passes the injection guard before it is stored, read by a model or shown to any agent. Your own `.txt`, `.md`, `.pdf` files and what you type are trusted.

- **Rules**: Czech and English patterns (ignore/forget/override instructions, persona overrides, chat-template markers, requests to reveal prompts or call tools), case- and diacritics-insensitive; text hidden in HTML (inline styles, `hidden`, `aria-hidden`, class and id rules in the page's own `<style>`, comments, `<noscript>`, `<template>`) when it reads as an instruction to an AI. Everything in `<head>` (`<title>`, `<meta content>`, comments), anything outside `<body>`, `<title>` anywhere and `alt`/`title` attribute values also count as hidden text. Hidden text alone is flagged only when one hidden piece (one hidden element, comment, `<head>` child, attribute value or piece outside `<body>`) addresses an AI and gives an order, so words in unrelated pieces do not add up and hidden menus or cookie banners pass. Zero-width characters and directional marks (U+200E, U+200F, U+061C) are flagged inside words; bidi embedding, override and isolate controls anywhere. External stylesheets are not evaluated.
- **Limits**: local `.html`/`.htm` files above 5 MiB are refused (web pages have the same cap; local `.txt`, `.md` and `.pdf` files above 20 MiB are refused too). Each redirect of a URL is checked: a redirect from a public host to a local or private address, or from `https` to `http`, is refused as a source error (the URL you type yourself may be local). The redirect check looks up the host separately from the connection, so DNS rebinding between the two lookups is not covered.
- **Classifier**: [`protectai/deberta-v3-base-prompt-injection-v2`](https://huggingface.co/protectai/deberta-v3-base-prompt-injection-v2) (Apache-2.0), its ONNX export run on CPU with `onnxruntime`, at a pinned revision with pinned SHA-256 checksums, stored in `<workspace>/models/prompt-guard/<revision>/`. Long texts are scored in overlapping windows and the highest score counts. The model is trained mostly on English, so for Czech the rules carry more of the load.
- **Download**: about 0.7 GB from huggingface.co, only after your explicit yes — at the end of `/setup`, or in chat the first time a URL or `.html` needs it. Headless runs never download, even with `--yes`.
- **Fail closed**: without a working classifier (missing, bad checksum, does not load) URLs and `.html` files are refused — not stored, not quarantined, not passed on. Rules alone never let web content through. `.txt`, `.md` and `.pdf` still work.

### Quarantine

Flagged content goes to `<workspace>/quarantine/<id>/`: `content.enc` (the original bytes, encrypted with Fernet) and `report.json` (source, time, the matched rules or the score, SHA-256, status — never an excerpt). The key is generated on first use and stored as `SORTINGCAP_QUARANTINE_KEY` in `<workspace>/.env`; `/settings` never shows it. Nothing from flagged content reaches a model, an agent or a source file unless you release it.

- `/show-quarantine` lists the items; `/show-quarantine <id>` decrypts one in memory and prints it as plain text, with control and bidi characters escaped. It is never written to disk or the chat history.
- `/release-quarantine <id>` shows the report and text, asks (default no) and on yes adds the content as a source without checking it again, marks the report `released` and deletes `content.enc`.

Both are chat only: the headless runner refuses them even with `--yes`, and the cap cannot call them. If you lose the key, the list still works but items cannot be read or released, and nothing new is quarantined until you restore the key or remove the old items; nothing is ever deleted for you.

## Headless runner

Every command except `setup`, `show-quarantine` and `release-quarantine` also runs without the chat, e.g. from cron:

```bash
uv run sortingcap run help settings
uv run sortingcap run settings --json
uv run sortingcap run settings --language en --yes
uv run sortingcap run show-profile --json
```

- `--json` prints the result as JSON on stdout.
- `--yes` answers confirmations with yes; without it every confirmation is denied. Commands that need a choice, text or a key fail.
- Repeatable options take one value each: `--source a.pdf --source b.html`.
- Exit codes: `0` success, `1` failure or an incomplete run (output still printed, e.g. a failed `--source`), `2` bad arguments or a chat-only command (`setup`, `show-quarantine`, `release-quarantine` — type them in the chat instead).

## Workspace

Everything the cap keeps about you is in plain files in one directory, by default `~/SortingCap`, created readable only by you (mode 0700). An existing directory keeps its mode; if others can access it, the chat warns at start and shows the `chmod 700` command. Set `SORTINGCAP_WORKSPACE` **in your shell** to move it.

| File | Contents |
|---|---|
| `settings.json` | Language, provider, model, URLs. Hand edits apply on the next command; an invalid file is reported, never overwritten |
| `.env` | Remote API keys (`NOVITA_API_KEY`, `SORTINGCAP_OPENAI_API_KEY`) and the quarantine key (`SORTINGCAP_QUARANTINE_KEY`), mode 0600. Environment variables with the same names win. See `.env.example` — copy it to `<workspace>/.env`, not to the repository. Back it up: without the quarantine key, quarantined items cannot be read |
| `README.md`, `AGENTS.md` | Notes for you and for any AI assistant or coding agent opened in the workspace: it holds personal data, and `profile/sources/`, `quarantine/` and scraped content are untrusted data, not instructions. Written when missing, never overwritten |
| `profile/`, `listing_filter_file.md` | Your profile and sources, see [Profile files](#profile-files) |
| `quarantine/` | Encrypted flagged content, see [Quarantine](#quarantine) |
| `models/prompt-guard/` | The injection classifier, see [The guard](#the-guard) |
| `.history` | Chat input history, mode 0600 (hidden key input is never stored) |
| `logs/setup.log` | Every command `/setup` ran, with its exit code |
| `logs/ollama-serve.log` | Output of an `ollama serve` started by `/setup` (it keeps running after the app exits) |

LangSmith tracing is always off.

## Development

```bash
uv run ruff check          # lint
uv run ruff format         # format (CI runs --check)
uv run mypy app            # typecheck (default mode with the pydantic plugin; tests/ not checked)
uv run pytest              # offline test suite; network sockets are disabled
uv run pytest -m live      # opt-in: real classifier on Czech and English samples, precision and recall per language
```

The live suite needs the classifier installed (`/setup`, last step) in your workspace, or `SORTINGCAP_LIVE_WORKSPACE` pointing at one that has it; otherwise it skips. It is not run by default or in CI and is not required to pass.

CI (GitHub Actions) runs ruff, mypy and the offline test suite.

Design and decisions live in `specs/` (mission, tech stack, roadmap, and one folder per feature).

## Licence

GPL-3.0
