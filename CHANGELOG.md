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
- `/add-information` tells the cap who you are: give it your CV and other files (`.txt`, `.md`, `.pdf`, `.html`) or web pages, introduce yourself, and answer its questions about what is still missing. Type `next` to skip a question and `done` to finish.
- `/add-information --source <file or URL>` (repeatable) adds sources without questions, in chat or from scripts with `sortingcap run add-information`.
- Your profile is a plain `profile/profile.md` you can edit by hand; your edits are kept. Copies of your sources live next to it, and files you drop into `profile/sources/` are picked up on the next round.
- The cap writes `listing_filter_file.md`, a summary of what you are looking for that later versions use to filter job listings. It is regenerated when your profile changes.
- `/show-profile` shows your profile, what is still missing, your sources and the filter file, and where each file is stored. The assistant can read it too.
- Web pages and `.html` files are checked for prompt injection by rules and a local classifier before anything reads them. `/setup` offers to download the classifier (about 0.7 GB); without it, web sources are refused rather than let through.
- Suspicious content goes into an encrypted quarantine. `/show-quarantine` lists it or shows one item, and `/release-quarantine <id>` lets it in as a source after your yes. Only you can do this, in the chat.
- `/settings --guard-threshold` sets how sure the classifier must be before content is quarantined.
- Chat commands start with `/` (`/help`, `/setup`, `/add-information`, …), like in Claude Code. A `/word` that is not a command gets an error with a "did you mean" suggestion instead of going to the assistant.
- Typing `/` opens a menu of commands with descriptions while you type, with the top match as grey ghost text: ↑/↓ to pick, Tab or → to take it, Enter to run it, Esc to close. After a command the menu offers its flags.
- `/help` lists every command on one line, shortened to fit the terminal.
- The workspace gets a `README.md` and `AGENTS.md` that warn other AI tools on your computer not to follow instructions found in your files.
