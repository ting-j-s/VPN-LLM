# LLM Agent Design for VPN-LLM

## Overview

A controlled LLM/Vibe Coding engineering proxy framework that allows users to
request code changes in natural language (e.g. "switch the default transport
from TCP to WebSocket"). The system calls an OpenAI-compatible LLM API to
generate a modification plan and code changes, then automatically validates
them.

**This is a controlled tool, not an unconstrained auto-coder.** Every step
includes safety boundaries, verification gates, and audit reports.

## Architecture

```
User Request (natural language)
       |
       v
  TaskPlanner           -- classify intent, generate structured TaskPlan
       |
       v
  SafetyGuard           -- check blocked paths and commands BEFORE changes
       |
       v
  Plan generation       -- LLM generates structured modification plan
       |
       v
  Controlled execution  -- apply changes within allowed paths only
       |
       v
  ValidationRunner      -- compileall, targeted tests, full tests
       |
       v
  Failure recovery      -- if validation fails, feed errors back to LLM (max 3 retries)
       |
       v
  Report output         -- structured report of what was done and results
```

## Key Principles

### Safety Boundaries

- **No automatic `git push`**: All changes stay local; pushing requires manual user action.
- **No reading or modifying `.env`**: Prevents credential leaks.
- **No reading or modifying `.claude/`**: Prevents config/token tampering.
- **No reading or modifying private key files** (`*.key`, `id_rsa`, `id_ed25519`, etc.).
- **No reading or modifying API key files**.
- **No reading or modifying real certificates** (`cert.pem`, `key.pem` in config paths).
- **No dangerous shell commands**: `sudo`, `rm -rf`, `curl | bash`, `wget | bash`, `git push` are blocked.

### Protocol Change Synchronization

When changing the transport protocol (e.g. TCP -> WebSocket), the agent MUST also:

1. Update `config/server.yaml` and `config/client.yaml` (or equivalent transport-specific configs).
2. Update `README.md` if it references the old transport default.
3. Update or add relevant tests in `tests/`.
4. Run the full test suite before reporting success.

### Verification Pipeline

Every modification must pass:

| Stage | Command | Purpose |
|-------|---------|---------|
| 1. Compile check | `python3 -m compileall src tests` | Syntax validity |
| 2. Targeted tests | `python3 -m pytest tests/<relevant> -v` | Feature-specific checks |
| 3. Full test suite | `python3 -m pytest tests/ -v` | Regression check |
| 4. Git status | `git status --short` | Audit what changed |

Optional:
- Cross-process smoke test (TCP/WebSocket/TLS end-to-end with MockTun)

## Module Design

### `src/llm/validation_runner.py` — ValidationRunner

Runs shell commands and captures structured results.

- `run_command(cmd)` -> `ValidationResult` (returncode, stdout, stderr)
- `run_compileall()` -> `ValidationResult`
- `run_targeted_tests(test_paths)` -> `ValidationResult`
- `run_full_tests()` -> `ValidationResult`
- `run_git_status()` -> `ValidationResult`

### `src/llm/safety_guard.py` — SafetyGuard

Enforces security boundaries before any file write or command execution.

- `validate_write_path(path)` — blocks sensitive paths
- `validate_command(command)` — blocks dangerous commands
- Raises `SafetyError` on violation

Blocked file patterns:
- `.env`
- `.claude/` (directory or files within)
- `*.key` (private keys)
- `id_rsa`, `id_ed25519`, `id_ecdsa` (SSH private keys)
- `cert.pem`, `key.pem` (certificate material)
- Files in `.git/` (git internals)

Blocked command patterns:
- `sudo`
- `rm -rf` (and variants like `rm -r`, `rm -fr`)
- `curl ... | bash`, `wget ... | bash`, `curl ... | sh`
- `git push` (automatic push is disallowed)

### `src/llm/task_planner.py` — TaskPlanner

Rule-based classification of user requests (MVP: no real LLM call).

Task types:
- `transport_change` — detected when request mentions websocket/tcp/tls/ssh
- `config_change` — detected when request mentions config/configuration
- `test_addition` — detected when request mentions test/add test
- `docs_update` — detected when request mentions doc/readme/documentation
- `unknown` — fallback

Extracts `target_transport` from keywords: websocket, tcp, tls, ssh, mock.

### `scripts/llm_task.py` — CLI Entry Point

```
python3 scripts/llm_task.py --request "switch default transport to WebSocket"
```

Flow:
1. Parse `--request`
2. TaskPlanner.plan() -> TaskPlan
3. Print plan
4. SafetyGuard checks
5. ValidationRunner runs compileall + targeted tests + full tests + git status
6. Print structured report

### `scripts/validate_llm_task.sh` — Validation Script

Runs compileall, targeted tests, full test suite, and git status. Serves as a
quick validation entry point for the LLM agent framework.

## MVP Scope

- TaskPlanner uses rule-based classification (no real LLM API call)
- `llm_task.py` performs plan + validation only; does NOT generate or apply code changes
- No automatic `git commit` or `git push`
- All safety boundaries enforced

## Future Extensions

- Real LLM integration for plan/code generation
- Automated modification within allowed paths
- Failure-feedback loop (retry on validation failure, max 3)
- Session audit log
- Dry-run preview mode
