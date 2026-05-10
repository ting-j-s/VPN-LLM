# Phase 10.1: Transport/Core Replacement Smoke Validation Matrix

## Goal

This phase builds a **unified smoke validation matrix** for verifying the minimal
runnability of Transport and Core replacements. It is the verification entry point
for LLM-driven protocol and kernel replacement.

When the LLM Agent generates or modifies a Transport or Core implementation, the
system can call this matrix to answer a single question:

> **"Does the replacement still pass minimum smoke?"**

This is *not* ordinary transport testing — it is a **replacement validation gateway**
designed to serve the LLM Agent workflow.

## Motivation

The VPN project has two replaceable dimensions:

| Dimension | What can change | Example |
|---|---|---|
| **Transport** | Outer protocol (wire format) | TCP → WebSocket, add HTTP/2, add QUIC |
| **Core** | VPN kernel (session, forwarding, frame) | Default → strict-session, alt-frame-codec |

When the LLM Agent proposes a replacement in either dimension, we need a fast,
deterministic answer to: "Is this minimally runnable?"

## Transport Dimension

| Transport | Smoke check | Status in CI |
|---|---|---|
| `mock` | In-process MockTransport inject/recv | Stable |
| `tcp` | localhost client/server send/recv | Stable |
| `tls` | localhost client/server with ephemeral certs | Stable |
| `websocket` | localhost client/server send/recv | Stable |
| `ssh` | **Always skipped** — requires external SSH server | N/A |

### Why SSH defaults to skip

SSH transport requires a running SSH server (`sshd`) on the target host with valid
credentials. This is not available in CI or local smoke runs. The matrix marks SSH
as `skip` with the message `requires external SSH server`.

Use `--include-ssh` to explicitly run the SSH smoke when an SSH server is available.

### TLS ephemeral certs

TLS smoke generates self-signed certificates in a `tempfile.mkdtemp()` directory.
These are cleaned up immediately after the check. No certs are persisted or committed.

## Core Dimension

### `default` (current)

The default core smoke verifies:

1. `ClientCore` and `ServerCore` are importable
2. `session_id` validation works — frames with wrong `session_id` are dropped
3. Core start/stop cycle completes without error

It uses `MockTransport` and `MockTunDevice` — no real TUN device is created.

### Extension interface

Each core registers via `CORE_SMOKE_REGISTRY`:

```python
_CORE_SMOKE_REGISTRY = {
    "default": _smoke_core_default,
    # Future entries:
    # "strict_session": _smoke_core_strict_session,
    # "alt_frame_codec": _smoke_core_alt_frame,
    # "experimental_forwarding": _smoke_core_experimental,
}
```

To add a new core smoke:
1. Implement a handler with signature `(transport_result: SmokeResult) -> SmokeResult`
2. Register it in `CORE_SMOKE_REGISTRY`
3. Run: `--cores default,new_core`

### Why no real TUN

Real TUN devices require `root` privileges and kernel `tun` module support.
The smoke matrix targets CI and developer laptops without root.
Core logic (session_id validation, start/stop, data path) is verified via
`MockTunDevice` instead.

## Usage

### Command line

```bash
# Default: mock, tcp, tls, websocket with default core
python3 scripts/smoke_replacement_matrix.py

# Specific transports
python3 scripts/smoke_replacement_matrix.py --transports mock,tcp,websocket --cores default

# Include SSH (when sshd is available)
python3 scripts/smoke_replacement_matrix.py --transports tcp,ssh --cores default --include-ssh

# JSON output (for LLM Agent consumption)
python3 scripts/smoke_replacement_matrix.py --transports mock,tcp,tls,websocket --cores default --json
```

### Text output

```
Transport/Core Smoke Matrix
============================================================
mock/default       PASS
tcp/default        PASS     0.00s
tls/default        PASS     0.03s
websocket/default  PASS     0.06s
ssh/default        SKIP     (requires external SSH server)
```

### JSON output

```json
{
  "results": [
    {
      "transport": "websocket",
      "core": "default",
      "status": "pass",
      "duration_sec": 0.06,
      "error": null
    }
  ],
  "summary": {
    "passed": 4,
    "failed": 0,
    "skipped": 1
  }
}
```

### Exit codes

| Code | Meaning |
|---|---|
| 0 | All non-skipped entries pass (or only skips) |
| 1 | At least one entry failed |

## Interpreting pass / fail / skip

| Status | Meaning |
|---|---|
| `pass` | Minimal send/recv roundtrip succeeded. The transport is runnable. |
| `fail` | Smoke check raised an exception or assertion error. The error field contains details. |
| `skip` | Pre-condition not met (e.g., SSH requires external server). Not a failure. |

## Integration with LLM Agent

The smoke matrix is designed to be called by the LLM Agent validation pipeline:

1. Agent proposes a Transport or Core patch
2. `git apply --check` passes
3. Human confirms `--apply-patch`
4. **Post-apply validation** calls the smoke matrix:
   ```bash
   python3 scripts/smoke_replacement_matrix.py --transports <new_transport> --cores <new_core> --json
   ```
5. If `summary.failed == 0`, the replacement is minimally runnable
6. If `summary.failed > 0`, the Agent reports the failure and the human reviews

This fits between the existing post-apply validation and commit advice steps.

## Future Extensions

### New Transports

| Transport | What needs to be done |
|---|---|
| HTTP/2 | Implement `src/transport/http2_transport.py`, add `_smoke_http2()`, register in `_TRANSPORT_SMOKE` |
| QUIC | Implement `src/transport/quic_transport.py`, add `_smoke_quic()`, register |
| gRPC | Implement `src/transport/grpc_transport.py`, add `_smoke_grpc()`, register |

### New Cores

| Core | What needs to be done |
|---|---|
| `strict_session` | Implement stricter session validation in Core, register smoke handler |
| `alt_frame_codec` | Implement alternative Frame encoding, register smoke handler |
| `experimental_forwarding` | Implement experimental NAT/route strategies, register smoke handler |

### Linux netns + real TUN validation

For integration tests that need real TUN devices without root on the host:

```bash
# In a network namespace, regular users can create TUN devices
ip netns add vpn-test
ip netns exec vpn-test python3 scripts/smoke_replacement_matrix.py --transports tcp --cores default
```

The smoke matrix does not currently create netns automatically.
This is a future enhancement for deeper integration testing.

### CI Integration

The smoke matrix can be added to `.github/workflows/tests.yml`:

```yaml
- name: Smoke replacement matrix
  run: python3 scripts/smoke_replacement_matrix.py --transports mock,tcp,websocket --cores default --json
```

TLS requires `openssl` in the CI environment (usually pre-installed).
WebSocket requires the `websockets` Python package.

## Files

| File | Purpose |
|---|---|
| `scripts/smoke_replacement_matrix.py` | Main script |
| `tests/test_smoke_replacement_matrix.py` | Tests (29 tests) |
| `docs/phase10_replacement_smoke_matrix.md` | This document |
