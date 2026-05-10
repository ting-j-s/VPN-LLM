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

## Phase 9.7: Optional LLM-Based Task Planner

### Overview

An optional LLM-based task planner (`LLMTaskPlanner`) that uses an
OpenAI-compatible API for **request understanding, task decomposition,
candidate file prediction, and validation command suggestion**.

**The LLM is ONLY used for planning — it cannot execute commands, write files,
modify code, commit, or push.**

### Key Design Decisions

- **Default OFF**: `agent.use_llm_planner` is `false`. Users must explicitly
  pass `--use-llm-planner` to the CLI.
- **API key from environment only**: The API key is read from an environment
  variable (e.g. `LLM_API_KEY`), never from config files or code.
- **Schema validation on ALL LLM output**: Every field is type-checked and
  whitelist-validated before acceptance.
- **SafetyGuard on ALL LLM output**: Every candidate file path is checked
  by `SafetyGuard.validate_write_path()`. Every validation command is checked
  by `SafetyGuard.validate_command()`. Any violation raises
  `LLMTaskPlannerError` — the plan is rejected, nothing is written.
- **Config file is git-ignored**: `config/llm_agent.yaml` is in `.gitignore`.
  Only the example file `config/llm_agent.yaml.example` is committed.

### LLM Output Schema

The LLM must return a JSON object with these fields:

| Field | Type | Validation |
|-------|------|------------|
| `task_type` | string | Must be in: transport_change, config_change, test_addition, docs_update, bugfix, refactor, unknown |
| `target_transport` | string or null | Must be in: tcp, tls, ssh, websocket, mock, or null |
| `summary` | string | Must be non-empty |
| `candidate_files` | list of strings | Each path checked by SafetyGuard.validate_write_path() |
| `validation_commands` | list of strings | Each command checked by SafetyGuard.validate_command() |
| `risk_level` | string | Must be in: low, medium, high |

### Failure Modes

If the LLM returns:
- **Invalid JSON** → `LLMTaskPlannerError("not valid JSON")`
- **Missing fields** → `LLMTaskPlannerError("missing required fields: [...]")`
- **Illegal task_type** → `LLMTaskPlannerError("Invalid task_type")`
- **Illegal transport** → `LLMTaskPlannerError("Invalid target_transport")`
- **Dangerous file path** → `LLMTaskPlannerError("unsafe file path")`
- **Dangerous command** → `LLMTaskPlannerError("unsafe command")`

All failures are hard errors — the system never silently accepts invalid or
dangerous LLM output.

### Configuration

See `config/llm_agent.yaml.example` for the full configuration format.

### CLI Usage

```
# Rule-based (default, no network)
python3 scripts/llm_task.py --request "..."

# LLM-based
python3 scripts/llm_task.py --request "..." --use-llm-planner
```

## Phase 9.8: LLM Patch Generation (Dry-Run Only)

### Overview

An optional LLM-based patch generator (`LLMPatchGenerator`) that produces unified
diffs for a given task plan. **The patch is saved to disk and validated via
`git apply --check` but is NEVER automatically applied, committed, or pushed.**

The LLM is ONLY used for diff generation — it cannot write files, apply patches,
commit, or push. The generated patch must pass strict format validation AND
SafetyGuard checks before being saved.

### Key Design Decisions

- **Requires `--generate-patch` AND `--use-llm-planner`**: Both flags must be
  present. `--generate-patch` alone exits with an error.
- **Dry-run only**: The CLI saves `patch.diff` and runs `git apply --check`.
  The patch is NOT applied. A human must review and manually apply it.
- **Dual safety validation**: Every file path in the diff is checked against
  SafetyGuard AND a patch-specific blocklist. Diff content is scanned for
  secrets.
- **Strict diff format**: The LLM output must contain `diff --git`, `--- a/`,
  and `+++ b/` headers. Non-diff output is rejected.

### Patch Blocklist

Beyond the standard SafetyGuard paths, the patch generator also blocks:
- `config/llm_agent.yaml` and `config/llm_agent.yaml.example`
- `.claude/` prefix paths
- `.git/` prefix paths
- `*.key`, `*.pem`, `*.crt` extensions

### Secret Scanning

The diff content is scanned for:
- `-----BEGIN ... PRIVATE KEY-----` (RSA, EC, DSA, OpenSSH)
- `-----BEGIN CERTIFICATE-----`
- `sk-...` API key patterns
- `AIza...` Google API keys
- `eyJ...` JWT tokens (base64url-encoded JSON)
- `api_key = "..."` / `api_key: "..."` assignments
- `password = "..."` / `password: "..."` assignments
- `Bearer ...` authorization tokens

Any match causes immediate rejection.

### Validation Pipeline (Extended)

| Stage | Command | Purpose |
|-------|---------|---------|
| 1. Compile check | `python3 -m compileall src tests` | Syntax validity |
| 2. Targeted tests | `python3 -m pytest tests/<relevant> -v` | Feature-specific checks |
| 3. Full test suite | `python3 -m pytest tests/ -v` | Regression check |
| 4. Git status | `git status --short` | Audit what changed |
| 5. Patch generation | LLM API call | Generate unified diff (optional) |
| 6. Git apply check | `git apply --check <patch.diff>` | Validate patch applicability |

### CLI Usage

```bash
# Generate patch (dry-run)
python3 scripts/llm_task.py --request "switch to websocket" --use-llm-planner --generate-patch

# Error: --generate-patch alone is rejected
python3 scripts/llm_task.py --request "switch to websocket" --generate-patch
# Error: --generate-patch requires --use-llm-planner
```

### Module: `src/llm/patch_generator.py`

- `LLMPatchGenerator(config_path)` — loads config, reads API key from env
- `generate(request, task_plan, repo_context) -> str` — returns validated diff
- `_extract_diff(raw)` — strips markdown fences, validates diff format
- `_parse_file_paths(diff_text)` — extracts file paths from diff headers
- `_validate_file_path(path)` — SafetyGuard + patch-specific blocklist
- `_scan_for_secrets(diff_text)` — rejects diffs containing secrets

### Task Record Extensions

- `patch.diff` — the raw generated unified diff
- `validation.json` now includes `git_apply_check` result
- `report.md` includes Patch Generation section with file list and status

### Report Extensions

When a patch is generated, the report includes:
- **Patch Generation** section with status (always "not applied"), size, file list
- **Git Apply Check** result section
- Conclusion note: "Patch was generated and saved as `patch.diff` — NOT applied."

## Phase 9.9: Human-Confirmed Patch Application

### Overview

An explicit opt-in mechanism (`--apply-patch`) for applying a generated and
validated patch to the working tree. **The patch is NEVER applied automatically.**
A human must explicitly pass `--apply-patch`. Post-apply validation runs
automatically, but the system never commits or pushes.

### Key Design Decisions

- **Explicit opt-in**: `--apply-patch` is required. Without it, patch generation
  remains dry-run only.
- **Pre-condition gates**: All of these must be true before apply:
  1. `--apply-patch` is passed
  2. `--generate-patch` is passed (enforced by CLI)
  3. `--use-llm-planner` is passed (enforced by CLI)
  4. `git apply --check` has succeeded
  5. Working tree is clean (unless `--allow-dirty-worktree` is passed)
- **No auto-commit, no auto-push**: The system applies the patch to the working
  tree, runs validation, and reports results. All committing and pushing is
  manual.
- **Git apply bypasses SafetyGuard command validation**: `run_git_apply()` uses
  a controlled argument list (`["git", "apply", patch_path]`) via subprocess
  without shell interpolation, so it is not subject to `validate_command()`
  interception. The patch path must be under `.llm_tasks/`.
- **Post-apply validation**: After apply, the system automatically runs:
  - `python3 -m compileall src tests`
  - LLM-suggested validation commands (only safe ones, re-checked by SafetyGuard)
  - Targeted tests for the target transport
  - `python3 -m pytest tests/ -v`
  - `git status --short`

### Clean Worktree Requirement

`ValidationRunner.ensure_clean_worktree()` runs `git status --porcelain`. If
the output is non-empty, a `DirtyWorktreeError` is raised. The CLI catches this
and exits with an error unless `--allow-dirty-worktree` is passed.

This prevents accidental application on top of uncommitted changes.

### Post-Apply Report Section

The report includes a **Patch Application** section showing:
- `Patch applied: Yes` / `Patch applied: No`
- Git apply returncode
- Post-apply validation results (compile check, targeted tests, full test suite, git status)
- Guidance: "Commit the changes manually when ready" on success, or
  "Do not commit until failures are fixed" on failure

### CLI Usage

```bash
# Default: dry-run only (Phase 9.8 behavior)
python3 scripts/llm_task.py --request "switch to websocket" --use-llm-planner --generate-patch

# Apply after check passes (requires clean worktree)
python3 scripts/llm_task.py --request "switch to websocket" --use-llm-planner --generate-patch --apply-patch

# Apply even if worktree is dirty
python3 scripts/llm_task.py --request "switch to websocket" --use-llm-planner --generate-patch --apply-patch --allow-dirty-worktree

# Error: --apply-patch requires --generate-patch
python3 scripts/llm_task.py --request "switch to websocket" --apply-patch
# Error: --apply-patch requires --generate-patch
```

### Module: `src/llm/validation_runner.py`

New methods:
- `ensure_clean_worktree()` — raises `DirtyWorktreeError` if working tree has uncommitted changes
- `run_git_apply(patch_path)` — applies patch via `["git", "apply", patch_path]` (no shell). Requires path under `.llm_tasks/`. Returns `ValidationResult`.

### Module: `src/llm/task_record.py`

New method:
- `save_apply_result(task_id, apply_result, post_apply_results)` — saves `apply_result.json` and `post_apply_validation.json`

### Module: `src/llm/report_writer.py`

Extended `write_report()` parameters:
- `apply_result` — `ValidationResult` from `git apply`, or `None`
- `post_apply_validation` — dict of label → `ValidationResult` for post-apply checks

### SafetyGuard

- `git push` remains blocked by `BLOCKED_COMMAND_PATTERNS`
- `run_git_apply()` uses a fixed argument list (not `shell=True`), so it does not pass through `validate_command()`. This avoids accidental blocking of `git apply`.
- Patch path must be under `.llm_tasks/` — arbitrary external paths are rejected

### Failure Handling

| Condition | Behavior |
|-----------|----------|
| `--apply-patch` without `--generate-patch` | CLI exits with error |
| `git apply --check` failed | CLI exits — nothing applied |
| Dirty worktree (no `--allow-dirty-worktree`) | CLI exits with `DirtyWorktreeError` |
| `git apply` fails | Patch not applied; report shows failure |
| Post-apply validation fails | Report says "Do not commit until failures are fixed" |
| Post-apply validation passes | Report says "Commit the changes manually when ready" |

### Artifacts

When `--apply-patch` is used, the task directory additionally contains:
- `apply_result.json` — git apply result
- `post_apply_validation.json` — post-apply validation results

## Future Extensions

- Failure-feedback loop (retry on validation failure, max 3)
- Session audit log
- Dry-run preview mode
