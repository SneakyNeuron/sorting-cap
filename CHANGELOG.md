# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Install Sorting Cap with `uv` and start it from the terminal as a chat, in Czech or English.
- A guided `/setup` that gets a local AI model working: it finds, starts or installs Ollama (natively or in Docker), picks a Qwen model that fits your computer, downloads it and checks that it answers. It always asks before running anything that needs admin rights.
- Novita, or any OpenAI-compatible service, can be used instead of a local model as an opt-in choice during setup.
- Type plain text to talk to the assistant, or use slash commands such as `/help`, `/tour`, `/settings` and `/exit`. The assistant can use only the app's own tools, and never setup or settings.
- `sortingcap run <command>` runs the same commands without the chat, for scripts, with `--json` output and `--yes` for confirmations.
- A private workspace folder (`~/SortingCap` by default) for your settings, credentials, chat history and setup log.
- A short app overview and setup tour in both languages.
- `/add-information` is a conversation in which you tell the cap who you are, in your own words. It knows what it still needs and asks about missing items first, then about gaps in what it knows only partly (a salary without a currency, say).
- File paths and links (`.txt`, `.md`, `.pdf`, `.html`, web pages) can go anywhere in a message: the cap adds and reads each one at once and tells you what it filled. For a file you describe vaguely ("my CV is somewhere in Downloads") it can search by name, and reads a file it found, or one that matches only by name, only after you confirm it. It searches only the default folders or a folder you named, never hidden folders inside them, and opens a link over plain `http://` only when you typed it that way.
- Type `done` (in Czech also `hotovo`) or press Ctrl-D to save and finish the conversation; Ctrl-C cancels the round without saving. If the model keeps failing, the round is saved after three failed turns in a row.
- `/add-information --source <file or URL>` (repeatable) adds sources without a conversation, in chat or from scripts with `sortingcap run add-information`.
- Your profile is a plain `profile/profile.md` you can edit by hand; your edits are kept. Next to it, `profile/checklist.json` records whether each item is missing, partly known or known, and where the cap learned it; hand edits of `profile.md` are taken into account. Copies of your sources live next to it, and files you drop into `profile/sources/` are picked up on the next round.
- The cap writes `listing_filter_file.md`, a summary of what you are looking for that later versions use to filter job listings. It is regenerated when your profile changes.
- `/show-profile` shows your profile with each item's state (missing, partly known with what is still missing, known) and where it was learned, your sources and the filter file, and where each file is stored. The assistant can read it too.
- Web pages and `.html` files are checked for prompt injection by rules and a local classifier before anything reads them. `/setup` offers to download the classifier (about 0.7 GB); without it, web sources are refused rather than let through.
- Suspicious content goes into an encrypted quarantine. `/show-quarantine` lists it or shows one item, and `/release-quarantine <id>` lets it in as a source after your yes. Only you can do this, in the chat.
- `/settings --guard-threshold` sets how sure the classifier must be before content is quarantined.
- Chat commands start with `/` (`/help`, `/setup`, `/add-information`, …), like in Claude Code. A `/word` that is not a command gets an error with a "did you mean" suggestion instead of going to the assistant.
- Typing `/` opens a menu of commands with descriptions while you type, with the top match as grey ghost text: ↑/↓ to pick, Tab or → to take it, Enter to run it, Esc to close. After a command the menu offers its flags.
- `/help` lists every command on one line, shortened to fit the terminal.
- The workspace gets a `README.md` and `AGENTS.md` that warn other AI tools on your computer not to follow instructions found in your files.
- Continuous integration runs the typechecker (mypy) alongside the linter and the tests.
