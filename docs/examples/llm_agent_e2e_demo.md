# LLM Agent 端到端演示

> **状态**：演示 — 未调用真实 LLM API。
> **API 说明**：本演示未调用 LLM API。以下所有工作流产物均为手动生成的代表性示例，格式与真实 Agent 管线产生的格式一致。
> 当通过 `config/llm_agent.yaml`（未提交）提供兼容 OpenAI 的 API 时，相同的工作流完全相同。

---

## 1. 演示目标

证明人机协作的 LLM Agent 框架可以在严格的安全边界下辅助实际项目开发：

- **不自动 `git add`** — Agent 从不暂存文件
- **不自动 `git commit`** — 始终由人工创建提交
- **不自动 `git push`** — 推送始终手动执行
- **不提交密钥** — `.llm_tasks/`、`config/llm_agent.yaml` 和 API 密钥被排除
- **每个补丁在应用前后都经过验证**

演示任务是低风险的（`test_addition`）：我们添加测试覆盖，而非生产代码。

---

## 2. 用户请求

```
给 WebSocketTransport 增加连续多帧顺序收发测试，验证 WebSocketTransport 能保持消息顺序
```

---

## 3. 规划器输出（`plan.json`）

**规划器类型**：`rule_based`（确定性，不调用 LLM）

规则引擎匹配关键词"测试" → `task_type = test_addition`

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

## 4. 安全检查（SafetyGuard）

| 检查 | 结果 |
|------|------|
| 任务类型 `test_addition` → 无 src/ 写入风险 | 通过 |
| 影响区域仅 `tests/` → 后缀允许列表通过 | 通过 |
| 影响区域不含 `config/llm_agent.yaml` | 通过 |
| 未触及 `.env` / `.claude` / `.git` 路径 | 通过 |
| `risk_level = low` → 无需额外门控 | 通过 |

SafetyGuard **未阻止**此任务。

---

## 5. 应用前验证（基线）

在**任何**代码更改之前运行，建立干净基线：

```
python3 -m compileall src tests          → returncode 0
python3 -m pytest tests/test_websocket_transport.py -v → 全部通过
python3 -m pytest tests/ -v              → 738 passed, 6 skipped
git status --short                       → (clean)
```

所有预检查通过。继续补丁生成。

---

## 6. 补丁生成（`patch.diff`）

补丁向 `TestWebSocketTransportRoundtrip` 类添加一个测试方法 `test_client_to_server_multiple_messages_preserve_order`。不涉及生产代码。

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

**补丁摘要**：+28 行，1 个新测试，0 行生产代码更改。

---

## 7. Dry-Run 验证（`git apply --check`）

补丁触及工作树之前，先运行 dry-run 检查：

```
$ git apply --check /tmp/patch.diff
returncode: 0
success: Yes
```

Dry-run 通过 — 补丁可干净应用到当前工作树。

---

## 8. 人工确认补丁应用（`--apply-patch`）

人工审查 diff 并显式传递 `--apply-patch`：

```
$ python3 scripts/llm_task.py ... --apply-patch
```

```
$ git apply patch.diff
returncode: 0
success: Yes
```

此时工作树包含新测试，但**没有任何内容被暂存或提交**。

---

## 9. 应用后验证

补丁应用后运行相同的检查：

```
python3 -m compileall src tests          → returncode 0
python3 -m pytest tests/test_websocket_transport.py -v → 全部通过
python3 -m pytest tests/ -v              → 739 passed, 6 skipped
git status --short                       → M tests/test_websocket_transport.py
```

新测试通过。无回归。Git 显示恰好一个修改文件（未暂存）。

---

## 10. 提交建议（`--suggest-commit`）

因为应用后验证通过且请求了 `--suggest-commit`，`CommitAdvisor` 生成：

**建议的提交信息**：
```
test(websocket): add sequential multi-frame order-preservation test
```

**更改的文件**：
- `tests/test_websocket_transport.py`

**Diff 统计**：`1 file changed, 28 insertions(+)`

> 提醒：Agent 仅**建议**提交信息。由人工审查、暂存和提交。

---

## 11. 最终测试结果

```
============================== 739 passed, 6 skipped in 2.86s ==============================
```

| 指标 | 应用前 | 应用后 |
|------|--------|--------|
| `tests/test_websocket_transport.py` | 全部通过 | +1 通过 |
| 完整测试套件 | 738 passed, 6 skipped | 739 passed, 6 skipped |
| 编译检查 | pass | pass |
| Git 状态 | clean | 1 个已修改（未暂存） |

---

## 12. 安全边界 — 全部保留

| 边界 | 状态 | 证据 |
|------|------|------|
| 不自动 `git add` | ✅ | `git status` 显示文件为未暂存 `M` |
| 不自动 `git commit` | ✅ | Agent 未创建提交 |
| 不自动 `git push` | ✅ | Agent 未执行推送 |
| 补丁中无密钥 | ✅ | 补丁仅涉及 `tests/` |
| `.llm_tasks/` 未提交 | ✅ | 通过 safety guard 在 `.gitignore` 中 |
| `config/llm_agent.yaml` 未提交 | ✅ | 已在 `.gitignore` 中 |
| 输出中无 API key | ✅ | 经 `commit_summary.md` 检查确认 |
| 生产代码未更改 | ✅ | 补丁仅涉及 `tests/` |

---

## 13. Agent 产生的产物（在 `.llm_tasks/task_*` 中）

```
.llm_tasks/task_20260510_150000_testaddition/
├── request.txt              # 用户请求原文
├── plan.json                # 规则引擎生成的计划
├── repo_index_summary.json  # 仓库索引摘要
├── file_retrieval.json      # 文件检索结果
├── impact_analysis.json     # 影响分析
├── file_selection.json      # 最终文件选择（含 action_sources）
├── context_summary.json     # 上下文构建摘要
├── validation.json          # 修改前基线验证结果
├── patch.diff               # 生成的 diff（未应用前）
├── apply_result.json        # git apply 结果
├── post_apply_validation.json  # 应用后验证结果
├── suggested_commit_message.txt # 建议的提交信息
├── commit_summary.md        # 提交建议摘要
├── report.md                # 完整报告
└── status.json              # 任务状态
```

这些文件存储在 `.llm_tasks/` 下，该目录已被 git-ignored — **不会被提交**。

---

## 14. 摘要

本演示证明 LLM Agent 框架可以围绕真实项目任务自主规划、验证、生成补丁和建议提交，同时尊重每一个声明的安全边界。人工在每个决策点保持完全控制：

1. **审查**计划
2. **批准**补丁生成
3. **确认** `--apply-patch`
4. **审查**应用后结果
5. **手动暂存和提交**

无论使用规则引擎规划器（本演示）还是基于 LLM 的规划器（当 API 访问可用时），工作流同样有效。
