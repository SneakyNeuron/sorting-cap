# Bare-host local check: DNS pinning (follow-up to Phase 3)

Deferred by the developer on 2026-11-20 from the Phase 3 (`03-conversational-intake`) validation (finding D9). Security hardening, defence in depth only.

## Problem
- [ ] **D9** When `ingest_source` reads a bare `host/path` spec as `https://` + spec, `app/intake/round.py` checks the host with `is_local_host` (`app/intake/sources.py`), which resolves the name once. httpx resolves the name again when it fetches, and the first request is not covered by `_redirect_check` (that hook only checks redirects). A DNS answer that changes between the two lookups (DNS rebinding) therefore bypasses the check.

## Impact
Low: the user typed the host themselves in this round, so the check is defence in depth, not the main barrier.

## Fix options
- Pin the checked address in the transport: connect to the IP that was checked, keeping the original `Host` header and TLS SNI.
- Or re-check the resolved address in the request hook for URLs flagged by the bare-host rule, including the first request.

Related: the Phase 4 roadmap item on redirect and fetch safety (DNS rebinding between the redirect check and the connect) may solve both with one transport.
