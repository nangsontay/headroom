---
phase: 2
title: "Implement .env Loading"
status: pending
priority: P1
effort: "1h"
dependencies: []
---

# Phase 2: Implement .env Loading

## Overview

Add `.env` file loading to `headroom wrap` before proxy start and env var resolution. Two lookup locations: CWD (project-specific), then `~/.headroom/.env` (global fallback). CWD takes precedence.

## Architecture

```
headroom wrap claude [args]
  └─ load_headroom_dotenv()          ← NEW, called before _ensure_proxy()
       1. CWD/.env                   ← project-specific, highest priority
       2. ~/.headroom/.env           ← user global fallback
       (os.environ already set vars are NOT overwritten — shell wins)
  └─ _ensure_proxy()                 reads ANTHROPIC_TARGET_API_URL from env
  └─ env = os.environ.copy()         picks up loaded vars
  └─ subprocess.run(claude_bin, *claude_args, env=env)
```

`override=False` (default `load_dotenv` behavior) — existing shell env vars always win over `.env`. User can still override by setting vars in shell before running `wrap`.

## Related Code Files

- Modify: `headroom/cli/wrap.py`
  - Add `load_headroom_dotenv()` helper function
  - Call it at top of `claude()` command (before `_ensure_proxy()`)
  - Also call in `codex()`, `aider()` for consistency (same pattern)
- Modify: `pyproject.toml`
  - Add `python-dotenv>=1.0.0` to core `dependencies` list (line ~62)

## Implementation Steps

1. **Add `python-dotenv` to `pyproject.toml` core deps** (line 62, after `tomli` entry):
   ```toml
   "python-dotenv>=1.0.0",   # .env file loading for wrap commands
   ```

2. **Add `load_headroom_dotenv()` to `wrap.py`** (near top of file, after imports):
   ```python
   def load_headroom_dotenv() -> None:
       """Load .env from CWD then ~/.headroom/.env (shell env always wins)."""
       try:
           from dotenv import load_dotenv
       except ImportError:
           return  # python-dotenv not installed, skip silently

       cwd_env = Path.cwd() / ".env"
       global_env = Path.home() / ".headroom" / ".env"

       # Load global first so CWD overrides it
       if global_env.is_file():
           load_dotenv(global_env, override=False)
       if cwd_env.is_file():
           load_dotenv(cwd_env, override=False)
   ```

3. **Call `load_headroom_dotenv()` at top of `claude()` command** (`wrap.py:2968`), before any env var reads:
   ```python
   def claude(...) -> None:
       load_headroom_dotenv()   # ← add this line
       # ... rest of function
   ```

4. **Repeat for `codex()` and `aider()` commands** — same one-liner at top of each.

## `.env` Example for MLaaS (document in README / help text)

```dotenv
# ~/.headroom/.env  or  <project>/.env
ANTHROPIC_TARGET_API_URL=https://mlaas.virtuosgames.com/proxy/anthropic
ANTHROPIC_API_KEY=abc_dont_use
ANTHROPIC_CUSTOM_HEADERS=x-api-key: <your-mlaas-key>

# Optional: model aliases
ANTHROPIC_DEFAULT_OPUS_MODEL=anthropic/claude-opus-latest
ANTHROPIC_DEFAULT_SONNET_MODEL=anthropic/claude-sonnet-latest
```

## Success Criteria

- [ ] `python-dotenv` added to `pyproject.toml` core deps
- [ ] `load_headroom_dotenv()` implemented in `wrap.py`
- [ ] Called at top of `claude()`, `codex()`, `aider()` commands
- [ ] `override=False` — shell env always wins
- [ ] Import failure (not installed) is silently skipped
- [ ] CWD `.env` overrides `~/.headroom/.env` for same key

## Risk Assessment

- **Low risk** — `load_dotenv(override=False)` never overwrites existing env, can't break existing users
- `python-dotenv` is a tiny, stable dependency (no transitive deps)
- Import is guarded — graceful degradation if somehow not installed

## Security Considerations

- `.env` should be in `.gitignore` — document this explicitly
- `override=False` prevents `.env` from hijacking user's intentional shell config
