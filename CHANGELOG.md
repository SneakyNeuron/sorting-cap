# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Install Sorting Cap with `uv` and start it from the terminal as a chat, in Czech or English.
- A guided `-setup` that gets a local AI model working: it finds, starts or installs Ollama (natively or in Docker), picks a Qwen model that fits your computer, downloads it and checks that it answers. It always asks before running anything that needs admin rights.
- Novita, or any OpenAI-compatible service, can be used instead of a local model as an opt-in choice during setup.
- Type plain text to talk to the assistant, or use dash-commands such as `-help`, `-tour`, `-settings` and `-exit`. The assistant can use only the app's own tools, and never setup or settings.
- `sortingcap run <command>` runs the same commands without the chat, for scripts, with `--json` output and `--yes` for confirmations.
- A private workspace folder (`~/SortingCap` by default) for your settings, credentials, chat history and setup log.
- A short app overview and setup tour in both languages.
