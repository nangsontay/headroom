---
phase: 1
title: "Research & Scope"
status: completed
priority: P1
effort: "30m"
dependencies: []
---

# Phase 1: Research & Scope

## Overview

Pre-plan research done inline. Findings documented here for reference.

## Key Findings

### Args pass-through — already works
- `headroom/cli/wrap.py:2953` — `@click.argument("claude_args", nargs=-1, type=click.UNPROCESSED)`
- `headroom/cli/wrap.py:3177` — `subprocess.run([claude_bin, *claude_args], env=env)`
- `context_settings={"ignore_unknown_options": True}` on the command (line 2901)
- **No code change needed.** `headroom wrap claude --dangerously-skip-permissions` just works.

### .env loading — not implemented
- No `load_dotenv` call in wrap or proxy paths
- `python-dotenv` not in `pyproject.toml` core deps

### Upstream URL override — already has env var support
- `registry.py:108` — `ANTHROPIC_TARGET_API_URL` → proxy upstream
- `wrap.py:368–369` — `_start_proxy()` passes `--anthropic-api-url` to proxy subprocess when set
- Setting `ANTHROPIC_TARGET_API_URL` in `.env` is sufficient

### Custom headers (x-api-key for MLaaS) — works via ANTHROPIC_CUSTOM_HEADERS
- Claude Code reads `ANTHROPIC_CUSTOM_HEADERS` and sends them on every API call to proxy
- `anthropic.py:663–670` — proxy copies ALL inbound headers to upstream, only strips `host`, `content-length`, `accept-encoding`, `x-headroom-*`
- `wrap.py:3161` — `_apply_project_header_env()` APPENDS `X-Headroom-Project` to existing value, user's `x-api-key: <key>` header survives

### Conclusion: only 1 real code change needed
Load `.env` from CWD (and optionally `~/.headroom/.env`) at the start of `_ensure_proxy()` or `claude()`, before env vars are read. No new abstractions needed.

## Success Criteria

- [x] Confirmed args pass-through works without changes
- [x] Confirmed `ANTHROPIC_TARGET_API_URL` + `ANTHROPIC_CUSTOM_HEADERS` route to MLaaS correctly
- [x] Identified `.env` load as only missing piece
