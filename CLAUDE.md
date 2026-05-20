# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## Karpathy Guidelines

Always invoke the `/karpathy-guidelines` skill at the start of every session and apply its principles throughout:

1. **Think Before Coding** — State assumptions explicitly. If uncertain, ask. Surface tradeoffs. If your code isn't a clear improvement, say so.
2. **Simplicity First** — Minimum code that solves the problem. No speculative features, abstractions, or "flexibility." If 200 lines could be 50, rewrite.
3. **Surgical Changes** — Touch only what you must. Don't improve adjacent code. Match existing style. Every changed line must trace to the user's request.
4. **Goal-Driven Execution** — Define success criteria. Loop until verified. "Add validation" → "Write tests for invalid inputs, then make them pass."

## Project Conventions

- All network targets are restricted to localhost. No third-party scanning.
- Tests must pass without root. Mock/external dependencies gracefully skipped when unavailable.
- Don't auto-commit, don't auto-push. Wait for explicit instruction.
- `pytest-asyncio` is not installed. Use `asyncio.run()` in sync test functions instead.
- `traces_after/` is uncommitted scratch output. Never stage it.
