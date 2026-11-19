# Mission

## Vision
Sorting Cap is a free, open-source, Czech-oriented job-hunting harness that runs locally in the terminal. You talk to a chatbot (the "cap") that learns who you are, scrapes the job sites you point it to, keeps only the leads worth your time, and for every listing you approve researches the company and drafts a motivation letter that has survived a sceptical review and a fact-check against your own data.

## Audience
- Job seekers in Czechia (Czech or foreign) who are comfortable running a terminal app and want a local, private assistant instead of browsing boards by hand.
- Contributors who read the specs and code; the project is GPL-3.0 and free for anybody to use.

## Problem
Finding the right job means checking many boards repeatedly, reading dozens of irrelevant listings, researching each company and writing a tailored letter for each one. Generic job boards do not know what the seeker really wants, and hosted AI tools send personal data to third parties.

## Scope
Sorting Cap covers guided setup of a local LLM (Ollama); profile building from chat and from user sources (.txt, .pdf, .html, web pages); a user-readable profile and a generated `listing_filter_file.md`; headless scraping of user-stored job links, on demand or periodically via crontab; LLM relevance filtering, structured extraction and deduplication of listings; approve / reject review of listings, with rejection reversible; per-company and per-job folders with company research (web and reviews); motivation letters driven by user-editable letter skills and refined by a reflection loop (seller, sceptic, fact-checker); prompt-injection scanning of all web-sourced input.

## Success
- A user goes from install to a reviewed list of relevant listings with a single guided chat session, without editing config by hand.
- Filtering maximises both recall and precision: approved leads are a meaningful share of what is shown, and the user rarely finds a fitting listing on their boards that the cap dropped.
- Every claim in a generated letter is traceable to the user's own files or the company files; the fact-checker flags anything that is not.
- Content flagged as prompt injection is stored only encrypted in quarantine; it reaches an agent, a source file or the index only after the user explicitly releases it in chat.
- Everything the cap knows about the user is in plain files the user can read and edit.

## Tone & principles
- Voice: short, dry, a bit hat-like. Czech by default, English available.
- Local and private by default: the user's data stays on their machine; a remote LLM provider is opt-in.
- Files are the source of truth: profile, company and job folders, letters and letter skills are plain Markdown/JSON the user may edit by hand, and the app picks up those edits. The exception is `listing_filter_file.md`: it is plain Markdown the user can read, but it is generated only, overwritten on each regeneration and changed through `-add-information`.
- One tool registry: dash-commands in chat, agent tool calls and cron all trigger the same headless jobs; chat only starts them and shows the result.
- Untrusted by default: all web input is scanned and, if suspicious, quarantined (encrypted) before anything else touches it; only the user can release it.
- Prompts and output schemas are data, not code: every prompt lives in one prompt directory tree and every LLM output format is a Pydantic model.
- The user decides: the cap proposes, the user approves; nothing is sent or applied for automatically.
