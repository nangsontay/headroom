---
title: "headroom wrap: .env loading + upstream URL config"
description: "Load .env files in headroom wrap so users can configure ANTHROPIC_TARGET_API_URL, ANTHROPIC_CUSTOM_HEADERS, etc. without touching shell profile. Args pass-through to claude already works."
status: pending
priority: P2
branch: "main"
tags: ["wrap", "proxy", "dotenv", "mlaas"]
blockedBy: []
blocks: []
created: "2026-06-23T05:50:28.156Z"
createdBy: "ck:plan"
source: skill
---

# headroom wrap: .env loading + upstream URL config

## Overview

`headroom wrap claude` currently inherits env vars from the shell session only. Users running against an internal MLaaS proxy (e.g. Virtuos MLaaS) need to configure `ANTHROPIC_TARGET_API_URL`, `ANTHROPIC_CUSTOM_HEADERS` (for `x-api-key`), and `ANTHROPIC_API_KEY` (dummy) without hard-coding them in shell profiles.

**Args pass-through (`headroom wrap claude <args>`) is already implemented** — `claude_args: tuple` with `nargs=-1, UNPROCESSED` passes all extra args directly to `subprocess.run([claude_bin, *claude_args])`. No code change needed, just documentation.

## Target flow (MLaaS)

```
.env → headroom wrap claude → headroom proxy (:8787) → mlaas.virtuosgames.com/proxy/anthropic
                                                         (x-api-key forwarded from Claude Code headers)
```

## Key findings

- `ANTHROPIC_TARGET_API_URL` — already read by `resolve_api_overrides()` in `registry.py:108`; passed to proxy subprocess env + `--anthropic-api-url` flag
- `ANTHROPIC_CUSTOM_HEADERS` — Claude Code reads this and injects into every request to the proxy; proxy forwards all headers except `host`, `content-length`, `accept-encoding`, `x-headroom-*` → `x-api-key` flows through to MLaaS upstream
- `_apply_project_header_env()` in wrap.py appends `X-Headroom-Project: <cwd>` to existing `ANTHROPIC_CUSTOM_HEADERS` value — user's `x-api-key` header survives
- `python-dotenv` not in core deps; must add

## Phases

| Phase | Name | Status |
|-------|------|--------|
| 1 | [Research & Scope](./phase-01-research-scope.md) | Completed (pre-plan) |
| 2 | [Implement .env Loading](./phase-02-implement-env-loading.md) | Pending |
| 3 | [Verify Args Pass-through](./phase-03-verify-args-pass-through.md) | Pending |
| 4 | [Test & Validate](./phase-04-test-validate.md) | Pending |

## Dependencies

None.
