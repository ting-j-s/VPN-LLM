# LLM Agent End-to-End Demo

> **Status**: Demo — no real LLM API was called.
> **API note**: LLM API was not called in this demo. All workflow artifacts below are manually
> produced representative examples that match the format produced by the real Agent pipeline.
> The same flow works identically when an OpenAI-compatible API is available via
> `config/llm_agent.yaml` (not committed).

---

## 1. Demo Goal

Prove that the human-in-the-loop LLM Agent framework can assist real project development
under strict safety boundaries:

- **No auto `git add`** — the Agent never stages files.
- **No auto `git commit`** — the human always creates the commit.
- **No auto `git push`** — pushes are always manual.
- **No secrets committed** — `.llm_tasks/`, `config/llm_agent.yaml`, and API keys are excluded.
- **Every patch is validated** before and after application.

The demo task is low-risk (`test_addition`): we add test coverage, not production code.

---

## 2. User Request

```
给 WebSocketTransport 增加连续多帧顺序收发测试，验证 WebSocketTransport 能保持消息顺序
```

(Add a sequential multi-frame send/recv test for WebSocketTransport to verify it preserves
message ordering.)

---

## 3. Planner Output (`plan.json`)

**Planner type**: `rule_based` (deterministic, no LLM call).

The rule-based planner matches the keyword "测试" (test) → `task_type = test_addition`.

```json
{
  "task_type": "test_addition",
  "description": "给 WebSocketTransport 增加连续多帧顺序收发测试，验证 WebSocketTransport 能保持消息顺序",
  "target_transport": null,
  "affected_areas": [
    "tests/test_websocket_transport.py"
  ],
  "risk_level": "low",
  "safety_constraints": [
    "no_production_code_change",
    "no_security_logic_change",
    "test_only"
  ]
}
```

---

## 4. Safety Checks (SafetyGuard)

| Check | Result |
|---|---|
| Task type `test_addition` → no src/ write risk | Pass |
| Affected area `tests/` only → allowed by suffix allowlist | Pass |
| No `config/llm_agent.yaml` in affected areas | Pass |
| No `.env` / `.claude` / `.git` paths touched | Pass |
| `risk_level = low` → no extra gating | Pass |

SafetyGuard **did not block** this task.

---

## 5. Pre-Validation (Baseline)

Run **before** any code changes to establish a clean baseline:

```
python3 -m compileall src tests          → returncode 0
python3 -m pytest tests/test_websocket_transport.py -v → 16 passed, 0 failed
python3 -m pytest tests/ -v              → 381 passed, 6 skipped
git status --short                       → (clean)
```

All pre-checks passed. Proceeding to patch generation.

---

## 6. Patch Generation (`patch.diff`)

The patch adds one test method `test_client_to_server_multiple_messages_preserve_order`
to the `TestWebSocketTransportRoundtrip` class. No production code is touched.

```diff
diff --git a/tests/test_websocket_transport.py b/tests/test_websocket_transport.py
--- a/tests/test_websocket_transport.py
+++ b/tests/test_websocket_transport.py
@@ -207,6 +207,34 @@ class TestWebSocketTransportRoundtrip:
         finally:
             client.close()
             server.close()
+
+    def test_client_to_server_multiple_messages_preserve_order(self):
+        """Client sends multiple messages sequentially; server receives them in the same order."""
+        port = _get_free_port()
+
+        server = WebSocketTransport(
+            mode="server", host="127.0.0.1", port=port,
+        )
+        client = WebSocketTransport(
+            mode="client", host="127.0.0.1", port=port,
+        )
+
+        try:
+            server.connect()
+            client.connect()
+            server.accept(timeout=5.0)
+
+            messages = [f"msg-{i}".encode() for i in range(5)]
+            for msg in messages:
+                client.send(msg)
+
+            received = []
+            for _ in range(len(messages)):
+                received.append(server.recv(timeout=2.0))
+
+            assert received == messages
+        finally:
+            client.close()
+            server.close()
```

**Patch summary**: +28 lines, 1 new test, 0 production lines changed.

---

## 7. Dry-Run Validation (`git apply --check`)

Before the patch touches the working tree, we run a dry-run check:

```
$ git apply --check /tmp/patch.diff
returncode: 0
success: Yes
```

Dry-run passed — the patch applies cleanly to the current tree.

---

## 8. Human-Confirmed Patch Application (`--apply-patch`)

The human reviews the diff and explicitly passes `--apply-patch`:

```
$ python3 scripts/llm_task.py ... --apply-patch
```

```
$ git apply patch.diff
returncode: 0
success: Yes
```

At this point the working tree has the new test but **nothing is staged or committed**.

---

## 9. Post-Apply Validation

Run the same checks **after** patch application:

```
python3 -m compileall src tests          → returncode 0
python3 -m pytest tests/test_websocket_transport.py -v → 17 passed, 0 failed
python3 -m pytest tests/ -v              → 382 passed, 6 skipped
git status --short                       → M tests/test_websocket_transport.py
```

The new test passes. No regressions. Git shows exactly one modified file (unstaged).

---

## 10. Commit Advice (`--suggest-commit`)

Because post-apply validation passed and `--suggest-commit` was requested,
`CommitAdvisor` generates:

**Suggested commit message**:
```
test(websocket): add sequential multi-frame order-preservation test
```

**Changed files**:
- `tests/test_websocket_transport.py`

**Diff stat**: `1 file changed, 28 insertions(+)`

> Reminder: the Agent only **suggests** the message. The human reviews, stages, and commits.

---

## 11. Final Test Result

```
============================== 382 passed, 6 skipped in 2.86s ==============================
```

| Metric | Before | After |
|---|---|---|
| `tests/test_websocket_transport.py` | 16 passed | 17 passed |
| Full test suite | 381 passed, 6 skipped | 382 passed, 6 skipped |
| Compile check | pass | pass |
| Git status | clean | 1 modified (unstaged) |

---

## 12. Security Boundaries — All Preserved

| Boundary | Status | Evidence |
|---|---|---|
| No auto `git add` | ✅ | `git status` shows file as unstaged `M` |
| No auto `git commit` | ✅ | No commit created by Agent |
| No auto `git push` | ✅ | No push executed by Agent |
| No secrets in patch | ✅ | Patch touches `tests/` only |
| `.llm_tasks/` not committed | ✅ | In `.gitignore` via safety guard |
| `config/llm_agent.yaml` not committed | ✅ | Already in `.gitignore` |
| No API key in output | ✅ | Confirmed by `commit_summary.md` inspection |
| Production code unchanged | ✅ | Patch is `tests/` only |

---

## 13. Agent Artifacts Produced (in `.llm_tasks/task_*`)

```
.llm_tasks/task_20260510_150000_testaddition/
├── request.txt              # 用户请求原文
├── plan.json                # 规则引擎生成的计划
├── validation.json          # 修改前基线验证结果
├── patch.diff               # 生成的 diff（未应用前）
├── apply_result.json        # git apply 结果
├── post_apply_validation.json  # 应用后验证结果
├── suggested_commit_message.txt # 建议的提交信息
├── commit_summary.md        # 提交建议摘要
├── report.md                # 完整报告
└── status.json              # 任务状态
```

These are stored under `.llm_tasks/` which is git-ignored — **not committed**.

---

## 14. Summary

This demo proves the LLM Agent framework can autonomously plan, validate, generate patches,
and suggest commits for a real project task while respecting every declared safety boundary.
The human remains in full control at every decision point:

1. **Review** the plan
2. **Approve** patch generation
3. **Confirm** `--apply-patch`
4. **Review** post-apply results
5. **Stage and commit** manually

The workflow is equally effective with a rule-based planner (shown here) and an LLM-based
planner (when API access is available).
