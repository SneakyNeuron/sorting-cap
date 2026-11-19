# Roadmap

Phases are medium-sized by choice. Each is planned with `/sdd:feature`, built with `/sdd:implement` in task groups, and closed with `/sdd:validate`.

## Phase 1: Chat shell, tool registry and setup ✅
Spec: specs/features/01-chat-shell-setup/
Goal: a user can install the app with uv, run a guided `-setup` that gets a local LLM working, and get the app tour, all from the terminal chat.
- [x] Scaffold with uv (`pyproject.toml`, `uv.lock`, Python 3.12); `.gitignore` (including `.env`) and `.env.example` for the optional remote-provider key; ruff config; CI running ruff and pytest
- [x] Tool registry with Pydantic inputs/outputs, exposed as dash-commands, as agent tools and through the headless runner `sortingcap run <command>`
- [x] Terminal chat REPL: dash-commands run tools directly, free text goes to an agent that can call only registered tools; `-help` lists commands
- [x] User workspace directory and settings (language cs/en, model, provider)
- [x] `-setup`: detect or install/start Ollama (native or Docker), pick and pull a Qwen tier matched to the hardware, verify it; Novita or a custom OpenAI-compatible endpoint as opt-in menu options
- [x] Prewritten app overview and setup tour per language
- [x] `prompts/` tree per language and agent; README updated

## Phase 2: Profile intake and filter file
Spec: specs/features/02-profile-intake/
Goal: the user tells the cap about themselves by chat and sources, and gets reviewed profile files plus a `listing_filter_file.md`.
- [ ] Prompt-injection guard: local classifier + rules, quarantine directory, report to user; used by every later phase for web input
- [ ] `-add-information`: chat dialog and sources (.txt, .md, .pdf, .html, URL); PDFs read via LlamaIndex, HTML via beautifulsoup4; web sources through the guard
- [ ] Must-know checklist (target roles, past experience, …) and nice-to-have checklist (CV, salary range, contract type HPP/IČO/…, locations, work mode); the cap asks for missing items
- [ ] High-quality profile files in the workspace; manual files dropped into the workspace are picked up
- [ ] User can say "next" anytime; the cap tells where every profile file is stored
- [ ] `listing_filter_file.md` (what the user is looking for and all derived filters) regenerated after every intake round; intake loop can be re-entered anytime

## Phase 3: Scrape pipeline
Goal: `-scrape-jobs` turns the user's stored links into a deduplicated store of relevant, structured listings.
- [ ] `-add-web-links`: store and list job-board / career-page links
- [ ] Threaded httpx scraper with readability extraction, per-site Playwright fallback, robots.txt and rate limits
- [ ] All scraped content through the injection guard
- [ ] Relevance filter: dedicated filter model (verify `jaredpalmer/kev-27b`, fallback to chat model) with `listing_filter_file.md` + predefined prompt, tuned for recall and precision
- [ ] Structured extraction into the Pydantic listing schema (including listing language)
- [ ] Dedupe on position title + company + listing date against stored listings; store files + SQLite index with status `unresolved`
- [ ] Headless run summary shown in chat

## Phase 4: Review and scheduling
Goal: the user triages listings in chat and can run scrapes periodically.
- [ ] `-list-unresolved`, `-list-accepted`, `-list-rejected`
- [ ] Approve / reject commands; rejected listings can be reopened or approved later
- [ ] `-set-crontab`: install, show and remove crontab entries calling the headless runner for `scrape-jobs`

## Phase 5: Company research and motivation letters
Goal: approving a listing produces a company folder, job folder, company research and a fact-checked motivation letter.
- [ ] On approval: create company folder (if missing) and job subfolder with listing summary and original copy
- [ ] Company research: company website and employee reviews, through the injection guard, summarised into the company file
- [ ] Default letter skills shipped and copied to the workspace; user edits them by hand or via chat
- [ ] Letter generation in the listing's language (user override possible)
- [ ] Reflection loop: seller agent, sceptical company agent, fact-checker over the LlamaIndex index of user and company files; iteration cap; unsupported claims flagged
- [ ] Chat can discuss and regenerate a letter with the user's requested changes

## Later / unscheduled
- MCP server exposing the same tool registry
- Additional UIs (web, mobile) and a public API
- Multi-language beyond cs/en
- Employer side: companies describe a job, the LLM proposes whom to look for, and retrieves pre-generated candidate profiles and assessments via RAG
- [Phase 2 maintainability follow-ups](backlog/2026-11-19-phase2-maintainability.md): shared output helpers, graph node structure, test fixtures, typechecker
