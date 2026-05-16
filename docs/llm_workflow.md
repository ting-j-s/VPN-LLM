# LLM Agent Workflow — Agentized File Selection Pipeline

## 概述

VPN-LLM 的 LLM Agent 采用 **Agent 化的文件选择管线**。LLM 不再凭空猜测需要修改哪些文件，而是由本地仓库索引、多路召回和影响面扩展共同决定文件范围，LLM 只负责语义规划和 patch 生成。

## 核心原则

1. **LLM 不猜文件** — 文件发现完全由 RepoIndexer + FileRetriever + ImpactExpander 在本地完成
2. **LLM 做语义规划** — TaskPlanner 理解用户需求，生成 TaskPlan（含 requirements、constraints、validation_goals）
3. **LLM 做 patch 生成** — PatchGenerator 在 allowed_edit_files / allowed_create_paths 约束下生成修改
4. **所有 LLM 输出可审计** — candidate_files 只是 hints，不影响最终文件选择
5. **Dry-run first** — 所有修改先 dry-run，需人工 --apply-patch 确认
6. **永不自动 commit/push** — 版本控制操作始终需人工执行

## Agentized Flow

```
  User Request
      │
      ▼
  TaskPlanner (rule-based / LLM-based)
      → TaskPlan (task_type, target_transport, candidate_files as hints, ...)
      │
      ▼
  Extreme Risk Control
      → 检测高风险操作 → 停止 + clarification_questions.md
      │
      ▼
  RepoIndexer.build()
      → ast 提取 Python class/function/import
      → YAML/JSON config key 提取
      → 识别 test files (test_*.py, *_test.py)
      → 识别 doc files (README.md, docs/*.md)
      → 输出 RepoIndex (可序列化)
      │
      ▼
  FileRetriever.retrieve(request, plan)
      → 多路召回:
        1. 路径/文件名关键词匹配
        2. Python symbol 匹配
        3. Config key 匹配
        4. Task type 规则召回
        5. Test 文件映射
        6. Doc 文件映射
        7. LLM planner hints 合并（最低优先级）
      → 输出 CandidateFile[] (含 score, reasons, sources, action)
      │
      ▼
  ImpactExpander.expand(request, plan, candidates)
      → 规则驱动的 must_edit / must_review 扩展
      → LLM hints 存在文件 → 提升为 must_edit
      → LLM hints 不存在 → rejected_hints
      → Test / Doc 文件收集
      → Allowed create paths 确定
      → 输出 FileSelection
      │
      ▼
  ContextBuilder.build(file_selection)
      → 读取 must_edit + must_review 文件（12KB cap/file）
      → 附带 candidates 详情、allowed paths、rejected hints
      → 输出 repo_context string + ContextSummary
      │
      ▼
  PatchGenerator.generate(request, plan, repo_context,
                          allowed_edit_files, allowed_create_paths)
      → LLM 生成 edit blocks（FILE: + ACTION: replace/create）
      → 校验：FILE 必须在 allowed_edit_files 中（create 除外）
      → 校验：FIND 必须在目标文件中恰好出现 1 次
      → 校验：CREATE 必须在 allowed_create_paths 下
      → Secret scanning
      → 输出 patch.diff
      │
      ▼
  git apply --check (dry-run)
      │
      ▼
  --apply-patch (人工闸门)
      │
      ▼
  Post-apply Validation + Report + Commit Advice
```

## 任务类型 → 影响面映射

| task_type | 影响区域 | 示例 |
|---|---|---|
| `transport_change` | transport, config, test, docs | 替换外层协议 |
| `core_change` | core, tun, config, test, docs | 修改内核转发逻辑 |
| `config_change` | config, test, docs | 修改配置文件 |
| `test_addition` | test | 新增测试 |
| `docs_update` | docs | 更新文档 |
| `bugfix` | transport, core, tun, config, llm_agent, test, docs | Bug 修复（全范围） |
| `refactor` | transport, core, tun, config, llm_agent, test, docs | 重构（全范围） |
| `unknown` | mixed_feature | 不清楚需求（最大范围） |

## 新增数据结构

### CandidateFile
- `path`: 文件路径
- `score`: 相关性分数 (0-1)
- `reasons`: 匹配理由列表
- `sources`: 召回策略来源列表 (keyword/symbol/config_key/task_type/test_map/doc_map/planner_hint)
- `action`: 操作类型 (edit/review/test/doc)

### FileSelection
- `must_edit_files`: 必须编辑的文件
- `must_review_files`: 必须审查的文件
- `test_files`: 相关测试文件
- `doc_files`: 相关文档文件
- `allowed_create_paths`: 允许创建新文件的目录
- `candidates`: CandidateFile 列表
- `rejected_hints`: 被拒绝的 LLM planner hints（文件不存在）

## 安全边界

- **Extreme Risk Control** — 检测 .git/、.env、sudo、rm -rf、auto push 等极端风险，直接停止
- **allowed_edit_files 约束** — PatchGenerator 拒绝编辑不在列表中的文件
- **FIND 唯一性** — FIND 必须在目标文件中恰好出现 1 次
- **allowed_create_paths 约束** — 新文件只能在允许的目录下创建
- **Dry-run first** — 所有修改先 dry-run
- **永不自动 commit/push**

## 使用示例

```bash
# Rule-based planner（无需网络）
python3 scripts/llm_task.py --request "把传输协议换成 WebSocket"

# LLM-based planner + patch generation（dry-run only）
python3 scripts/llm_task.py \
  --request "add http2 transport support" \
  --use-llm-planner \
  --generate-patch

# Full flow：plan + file selection + patch gen + apply + smoke
python3 scripts/llm_task.py \
  --request "switch default transport to TLS" \
  --use-llm-planner \
  --generate-patch \
  --apply-patch \
  --run-replacement-smoke \
  --suggest-commit
```
