---
phase: 4
title: "Test & Validate"
status: pending
priority: P2
effort: "30m"
dependencies: [2, 3]
---

# Phase 4: Test & Validate

## Overview

Manual smoke test of the full MLaaS flow using `.env`. No new unit tests needed — `load_dotenv` behavior is already tested by the dotenv library itself.

## Implementation Steps

1. **Create `~/.headroom/.env`** with MLaaS config:
   ```dotenv
   ANTHROPIC_TARGET_API_URL=https://mlaas.virtuosgames.com/proxy/anthropic
   ANTHROPIC_API_KEY=abc_dont_use
   ANTHROPIC_CUSTOM_HEADERS=x-api-key: <real-mlaas-key>
   ```

2. **Verify proxy reads upstream URL:**
   ```bash
   headroom wrap claude --help   # should start without error
   # In another terminal:
   curl http://127.0.0.1:8787/health   # proxy should be up
   ```

3. **Verify request flows to MLaaS** — run a simple prompt and check proxy log:
   ```bash
   tail -f ~/.headroom/logs/proxy.log
   # Should show upstream target as mlaas.virtuosgames.com, not api.anthropic.com
   ```

4. **Verify CWD .env overrides global** — create `.env` in CWD with different `ANTHROPIC_TARGET_API_URL`, confirm proxy uses CWD value.

5. **Verify shell env wins over .env** — `export ANTHROPIC_TARGET_API_URL=https://other.example.com` then run wrap, confirm CWD `.env` does NOT override it.

6. **Verify args pass-through:**
   ```bash
   headroom wrap claude --dangerously-skip-permissions
   ```

7. **Run existing test suite** to catch regressions:
   ```bash
   uv run pytest tests/ -x -q --timeout=30
   ```

## Success Criteria

- [ ] `~/.headroom/.env` loaded → proxy forwards to MLaaS URL
- [ ] CWD `.env` overrides global `~/.headroom/.env`
- [ ] Shell env var wins over `.env` (override=False)
- [ ] `headroom wrap claude --dangerously-skip-permissions` works
- [ ] Existing test suite passes
- [ ] No `ANTHROPIC_CUSTOM_HEADERS` value corrupted by `_apply_project_header_env()` appending
