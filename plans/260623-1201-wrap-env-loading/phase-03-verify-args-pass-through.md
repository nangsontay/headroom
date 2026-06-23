---
phase: 3
title: "Verify Args Pass-through"
status: pending
priority: P2
effort: "15m"
dependencies: [2]
---

# Phase 3: Verify Args Pass-through

## Overview

Args pass-through to Claude already works. This phase confirms it and adds a brief note to CLI help text so users know about it.

## Key Evidence (no code change needed)

- `wrap.py:2901` — `context_settings={"ignore_unknown_options": True}` on the `wrap claude` command
- `wrap.py:2953` — `@click.argument("claude_args", nargs=-1, type=click.UNPROCESSED)`
- `wrap.py:3177` — `subprocess.run([claude_bin, *claude_args], env=env)`

All args after `headroom wrap claude` are collected verbatim and splat into the subprocess call. Example:

```bash
headroom wrap claude --dangerously-skip-permissions
headroom wrap claude --resume abc123
headroom wrap claude --model claude-opus-4-5 -p "fix the bug"
```

## Implementation Steps

1. **Verify manually** — run `headroom wrap claude --help` and confirm no clash with existing flags
2. **Optionally** improve help text on `claude_args` argument in `wrap.py:2953`:
   ```python
   @click.argument(
       "claude_args",
       nargs=-1,
       type=click.UNPROCESSED,
       metavar="[-- CLAUDE_ARGS]...",
   )
   ```
   And add to `@click.command` docstring: `\b\nAny extra arguments are forwarded to the claude CLI.`

## Success Criteria

- [ ] `headroom wrap claude --dangerously-skip-permissions` launches without error
- [ ] `headroom wrap claude --resume <id>` passes `--resume <id>` through to claude binary
- [ ] Help text clarifies args are forwarded
