# VPN-LLM LLM Agent 设计文档

## 概述

VPN-LLM 包含一个受控的 LLM/Vibe Coding 工程代理框架，允许用户用自然语言请求代码变更（例如"把默认传输协议从 TCP 改为 WebSocket"）。系统调用兼容 OpenAI 的 LLM API 生成修改计划和代码变更，并自动对其进行验证。

**LLM Agent 专为 VPN-LLM 中的 Transport/Core 替换任务设计，不是通用自动编码器。** 其主要职责是帮助生成、验证和审计外层传输协议层和 VPN 核心/数据平面行为的变更。每一步都包含安全边界、验证 Gate 和审计报告。

## 架构

```
用户请求（自然语言）
       │
       ▼
  TaskPlanner           -- 分类意图，生成结构化 TaskPlan
       │
       ▼
  Extreme Risk Control  -- 检测极端风险（.git/、.env、sudo、rm -rf、auto push 等）
       │
       ▼
  SafetyGuard           -- 对所有候选路径做安全检查
       │
       ▼
  RepoIndexer           -- 扫描仓库，AST 提取 Python symbols/imports、提取 config keys、识别 test/doc 文件（纯本地，不用 LLM）
       │
       ▼
  FileRetriever         -- 多路文件召回：关键词 + symbol + config key + task type 规则 + test/doc 映射
       │
       ▼
  ImpactExpander        -- 影响面扩展：生成 FileSelection（must_edit / must_review / test / doc / allowed_create）
       │
       ▼
  ContextBuilder        -- 读取 selected files 构建 LLM 上下文（12KB/file cap）
       │
       ▼
  PatchGenerator        -- LLM 生成补丁，受 allowed_edit_files / allowed_create_paths 约束，FIND 唯一性校验
       │
       ▼
  git apply --check     -- dry-run 校验 patch 是否可应用
       │
       ▼
  --apply-patch         -- 人工闸门（显式确认，不可跳过）
       │
       ▼
  Post-apply Validation -- compileall + pytest + git status
       │
       ▼
  Replacement Smoke     -- Gate 1：smoke matrix（MockTun，localhost）
       │
       ▼
  netns + TUN E2E       -- Gate 2/3：真实 TUN + IP 包转发验证（需 root，手动）
       │
       ▼
  Report + Commit Advice -- 结构化报告 + Conventional Commits 建议（永不自动提交）
```

## 替换导向设计

LLM Agent 围绕 VPN 系统的两个可替换维度构建：

### Transport 替换

当用户请求外层协议变更（例如 TCP → WebSocket、添加 TLS、切换到 SSH）时，Agent：

1. 将请求分类为 `transport_change`
2. 识别 `target_transport`
3. 生成针对 `src/transport/` 和相关配置的补丁
4. 通过 smoke matrix 使用目标 transport 进行验证
5. 继续通过 netns/TUN E2E 验证进行真实内核验证

### Core 替换

当用户请求 VPN Core 行为变更（例如修改 session 验证、更改 Frame 编解码器、修改转发策略）时，Agent：

1. 将请求分类为 `core_change`
2. 生成针对 `src/core/`、`src/common/frame.py` 等的补丁
3. 运行完整 smoke matrix（mock, tcp, tls, websocket），因为 Core 变更影响所有 transport 组合
4. 验证 session_id 隔离和 TUN 转发功能仍然正常

### 验证优先工作流

每次替换（无论是 Transport 还是 Core）都必须通过分层验证管线才能被视为完成：

```
pytest → compileall → smoke matrix → netns/TUN E2E → benchmark
```

Agent 从不跳过 Gate。每个 Gate 失败都会在报告中反映。

### 人机协作安全

- `--apply-patch` 是显式人工选择加入 — 从不自动执行
- `git push` 在 SafetyGuard 级别被阻止
- 提交信息仅为建议（`--suggest-commit`）
- 所有更改保持本地状态，直到人工显式提交和推送

## 核心原则

### 安全边界

- **不自动 `git push`**：所有更改保持本地；推送需要手动用户操作
- **不读取或修改 `.env`**：防止凭据泄露
- **不读取或修改 `.claude/`**：防止配置/token 被篡改
- **不读取或修改私钥文件**（`*.key`、`id_rsa`、`id_ed25519` 等）
- **不读取或修改 API key 文件**
- **不读取或修改真实证书**（配置路径中的 `cert.pem`、`key.pem`）
- **不执行危险 shell 命令**：阻止 `sudo`、`rm -rf`、`curl | bash`、`wget | bash`、`git push`
- **LLM 不再猜文件**：文件选择由 RepoIndexer + FileRetriever + ImpactExpander 本地完成，LLM 只做 patch 生成且受 allowed_edit_files 约束

### 协议变更同步

当更改传输协议（例如 TCP → WebSocket）时，Agent 必须同时：

1. 更新 `config/server.yaml` 和 `config/client.yaml`（或等效的 transport 特定配置）
2. 如果 README.md 引用了旧传输默认值，则更新之
3. 在 `tests/` 中更新或添加相关测试
4. 在报告成功之前运行完整测试套件

### 验证管线

每次修改必须通过：

| 阶段 | 命令 | 目的 |
|------|------|------|
| 1. 编译检查 | `python3 -m compileall src tests` | 语法有效性 |
| 2. 针对性测试 | `python3 -m pytest tests/<relevant> -v` | 功能特定检查 |
| 3. 完整测试套件 | `python3 -m pytest tests/ -v` | 回归检查 |
| 4. Git 状态 | `git status --short` | 审计变更内容 |

可选：
- 跨进程 smoke 测试（TCP/WebSocket/TLS 使用 MockTun 进行端到端测试）

## 模块设计

### `src/llm/task_planner.py` — TaskPlanner（规则引擎）

基于规则的请求分类（MVP：不调用真实 LLM）。

任务类型：
- `transport_change` — 请求提及 websocket/tcp/tls/ssh 时检测到
- `core_change` — 请求提及 core/session/frame/forwarding/tun 时检测到
- `config_change` — 请求提及 config/configuration 时检测到
- `test_addition` — 请求提及 test/add test 时检测到
- `docs_update` — 请求提及 doc/readme/documentation 时检测到
- `bugfix` — 请求提及 fix/bug/error/repair 时检测到
- `refactor` — 请求提及 refactor/restructure/clean 时检测到
- `unknown` — 回退类型

从关键词中提取 `target_transport`：websocket、tcp、tls、ssh、mock。

### `src/llm/llm_task_planner.py` — LLMTaskPlanner（基于 LLM）

可选的基于 LLM 的任务规划器，使用兼容 OpenAI 的 API 进行 **请求理解、任务分解、候选文件预测和验证命令建议**。

**LLM 仅用于规划 — 它不能执行命令、写文件、修改代码、提交或推送。**

关键设计决策：
- **默认关闭**：`agent.use_llm_planner` 为 `false`。用户必须显式传递 `--use-llm-planner` 给 CLI
- **API key 仅从环境变量读取**：API key 从环境变量（如 `LLM_API_KEY`）读取，从不在配置文件或代码中
- **对所有 LLM 输出进行 Schema 验证**：每个字段在接受之前都进行类型检查和允许列表验证
- **对所有 LLM 输出进行 SafetyGuard 检查**：每个候选文件路径和验证命令都由 SafetyGuard 检查

LLM 输出 Schema：

| 字段 | 类型 | 验证 |
|------|------|------|
| `task_type` | string | 必须在允许列表中 |
| `target_transport` | string 或 null | 必须在允许列表中或为 null |
| `summary` | string | 必须非空 |
| `candidate_files` | list of strings | 每个路径由 SafetyGuard 检查 |
| `validation_commands` | list of strings | 每个命令由 SafetyGuard 检查 |
| `risk_level` | string | 必须为 low、medium 或 high |

> **注意**：`candidate_files` 仅作为 hints（提示），最终文件选择由 RepoIndexer + FileRetriever + ImpactExpander 本地完成。

### `src/llm/repo_indexer.py` — RepoIndexer（仓库索引器）

纯本地仓库扫描器，不使用 LLM：

- 递归扫描仓库目录
- 忽略 .git、__pycache__、.llm_tasks、venv 等目录
- 忽略 .env、.key、.pem 等敏感文件
- 对 Python 文件使用 `ast` 提取 class/function/import
- 对 YAML/JSON 提取顶层 config keys
- 识别 test files（test_*.py、*_test.py）
- 识别 doc files（README.md、docs/*.md）
- 输出可序列化的 `RepoIndex`

### `src/llm/file_retriever.py` — FileRetriever（文件检索器）

多路文件召回策略（按优先级排序）：

| # | 策略 | 权重 | 说明 |
|---|---|---|---|
| 1 | 关键词匹配 | 0.90 | 请求中的关键词匹配文件路径 |
| 2 | Symbol 匹配 | 0.85 | 关键词匹配 Python class/function/import 名 |
| 3 | Config key 匹配 | 0.80 | 关键词匹配 YAML/JSON 顶层 key |
| 4 | Task type 规则 | 0.70 | 根据 task_type 召回预定义路径模式 |
| 5 | Test 文件映射 | 0.50 | 根据 task_type 映射相关测试文件 |
| 6 | Doc 文件映射 | 0.40 | 根据 task_type 映射相关文档文件 |

LLM planner hints 以 0.60 分数合并。

### `src/llm/impact_expander.py` — ImpactExpander（影响面扩展器）

规则驱动的影响面扩展，生成 FileSelection：

- `must_edit_files`：仅高置信度文件（规则结构性文件 + 评分 ≥0.9 的候选文件）
- `must_review_files`：相关但不一定修改的文件
- `test_files` / `doc_files`
- `allowed_create_paths`：创建新文件的目录前缀
- `allowed_create_patterns`：fnmatch glob 命名模式（如 `*_transport.py`、`test_*.py`）
- `action_sources`：每个文件分类来源的可审计追踪
- `rejected_hints`：LLM 猜的不存在的文件

### `src/llm/context_builder.py` — ContextBuilder（上下文构建器）

读取 selected files 构建 LLM 上下文：
- 每个文件最多 12KB（防止 token 溢出）
- 附带 candidates 详情、allowed paths、rejected hints
- 附带 test_files 和 doc_files 路径列表

### `src/llm/patch_generator.py` — PatchGenerator（补丁生成器）

使用 LLM API 生成统一 diff。**补丁保存到磁盘并通过 `git apply --check` 验证，但从不自动应用、提交或推送。**

关键约束：
1. **FILE 必须在 allowed_edit_files 中** — 否则拒绝（除非 ACTION: create）
2. **ACTION: create 必须在 allowed_create_paths 下** — 否则拒绝
3. **FIND 必须恰好出现 1 次** — 0 次或多次匹配均拒绝；空 FIND、纯空白 FIND 均拒绝
4. **敏感路径扫描** — .env、.git、.key、.pem、README.md（禁止 create）、隐藏文件、路径穿越均永远拒绝
5. **Secret scanning** — 扫描 private key、API key、password、JWT 等模式
6. **语义重试** — LLM patch action 错误时自动重试（Phase 11.2）
7. **reasoning_content 回退** — 支持 `reasoning_content` 字段回退到 `content` 字段（Phase 11.2）

### `src/llm/safety_guard.py` — SafetyGuard（安全守卫）

在任何文件写入或命令执行之前强制执行安全边界。

阻止的文件模式：
- `.env`
- `.claude/`（目录或内部文件）
- `*.key`（私钥）
- `id_rsa`、`id_ed25519`、`id_ecdsa`（SSH 私钥）
- `cert.pem`、`key.pem`（证书材料）
- `.git/` 中的文件（git 内部文件）

阻止的命令模式：
- `sudo`
- `rm -rf`（及变体如 `rm -r`、`rm -fr`）
- `curl ... | bash`、`wget ... | bash`、`curl ... | sh`
- `git push`（禁止自动推送）

### `src/llm/validation_runner.py` — ValidationRunner（验证执行器）

运行 shell 命令并捕获结构化结果：

- `run_command(cmd)` → `ValidationResult`（returncode、stdout、stderr）
- `run_compileall()` / `run_full_tests()` / `run_git_status()`
- `ensure_clean_worktree()` — 如果工作树有未提交更改则抛出 `DirtyWorktreeError`
- `run_git_apply(patch_path)` — 通过 `["git", "apply", patch_path]` 应用补丁（不使用 shell）

### `src/llm/replacement_validator.py` — ReplacementValidator（替换验证器）

编排 smoke matrix 运行：
- 根据 `target_transport` 和 `task_type` 选择 transports
- 运行 `smoke_replacement_matrix.py --json`
- 解析 JSON 输出
- 报告每个 transport 的 pass/fail/skip

### `src/llm/commit_advisor.py` — CommitAdvisor（提交建议器）

只读类，从不修改仓库：
- 生成 Conventional Commits 格式的提交信息
- 运行 `git diff --stat` 和 `git diff --name-only`
- 写入 `suggested_commit_message.txt` 和 `commit_summary.md`

提交信息格式：

| 任务类型 | 前缀 | 示例 |
|---------|------|------|
| `transport_change` | `feat(transport)` | `feat(websocket): switch default transport` |
| `core_change` | `feat(core)` | `feat(core): add strict session validation` |
| `config_change` | `config` | `config: update server port` |
| `test_addition` | `test` | `test: add websocket transport tests` |
| `docs_update` | `docs` | `docs: update transport documentation` |
| `bugfix` | `fix` | `fix: correct websocket frame handling` |
| `refactor` | `refactor` | `refactor: extract transport base class` |
| `unknown` | `chore` | `chore: apply changes` |

### `src/llm/task_record.py` — TaskRecordManager（任务记录管理器）

管理任务目录（`.llm_tasks/<task_id>/`），持久化：
- `request.txt` — 用户请求原文
- `plan.json` — 任务计划
- `patch.diff` — 生成的统一 diff
- `validation.json` — 基线验证结果
- `apply_result.json` — git apply 结果
- `post_apply_validation.json` — 应用后验证结果
- `replacement_validation.json` — smoke matrix 结果
- `repo_index_summary.json` — 仓库索引摘要
- `file_retrieval.json` — 文件检索结果
- `impact_analysis.json` — 影响分析结果
- `file_selection.json` — 最终文件选择（含 action_sources）
- `context_summary.json` — 上下文构建摘要
- `suggested_commit_message.txt` — 建议的提交信息
- `commit_summary.md` — 提交建议摘要
- `report.md` — 完整报告

### `src/llm/report_writer.py` — ReportWriter（报告生成器）

生成结构化 Markdown 报告，包含以下部分：
- Task Info
- Planned Changes
- File Selection（Phase 11.1+）
- Patch Generation（如果生成）
- Patch Application（如果应用）
- Post-apply Validation
- Replacement Smoke Validation（如果运行）
- Commit Advice（如果生成）
- Failure Summary
- Conclusion

### `scripts/llm_task.py` — CLI 入口

```
python3 scripts/llm_task.py --request "把默认传输协议换成 WebSocket"
```

完整参数门控：
- `--request` — 用户请求文本
- `--use-llm-planner` — 启用 LLM 规划器（默认为规则引擎）
- `--generate-patch` — 生成补丁（需要 `--use-llm-planner`）
- `--apply-patch` — 应用补丁（需要 `--generate-patch`）
- `--allow-dirty-worktree` — 允许脏工作树情况下应用
- `--run-replacement-smoke` — 运行 smoke matrix
- `--suggest-commit` — 生成提交建议（需要 `--apply-patch`）

## 验证 Gate 体系

| Gate | 目的 | 命令 | 自动化 |
|------|------|------|--------|
| **Gate 1** | 单元/集成测试 | `python3 -m pytest tests/ -v` | 完全自动化 |
| **Gate 2** | Replacement smoke matrix | `python3 scripts/smoke_replacement_matrix.py --json` | 完全自动化 |
| **Gate 3** | netns + TUN 环境验证 | `sudo bash scripts/phase10_netns_tun_validation.sh --transport tcp` | 半自动化（需 root） |
| **Gate 4** | netns + TUN E2E ping | `sudo bash scripts/phase10_netns_tun_validation.sh --transport tcp --e2e-ping` | 半自动化（需 root） |
| **Gate 5** | benchmark / stability | 计划中 | TODO |

## Phase 11 增强：本地索引驱动的文件选择

Phase 11.1 和 11.2 将 LLM Agent 工作流从"LLM 猜测文件"升级为"本地索引驱动的文件选择管线"：

### Phase 11.1 加固要点

- **must_edit / must_review 严格分区** — must_edit 仅包含高置信度文件；纯 planner hint 只能进入 must_review
- **allowed_create_paths 收紧** — 加入 fnmatch glob 命名模式；README.md 禁止 create；隐藏文件/路径穿越/危险后缀均拒绝
- **FIND 唯一性校验增强** — 空 FIND、纯空白 FIND 均立即拒绝
- **action_sources 可审计** — file_selection.json 记录每个文件的分类来源
- **Planner hints 不能直接授予 must_edit** — LLM planner 的 candidate_files 仅是 hints（0.6 分），必须经过 FileRetriever 多路召回 + ImpactExpander 评分才能进入 must_edit

### Phase 11.2 加固要点

- **Candidate 阈值过滤** — FileRetriever 候选文件必须达到分数阈值才能被考虑
- **空 FIND 拒绝** — PatchGenerator 严格拒绝空 FIND 和纯空白 FIND
- **reasoning_content 回退** — 支持 LLM 响应中 `reasoning_content` 字段回退到 `content` 字段
- **语义重试** — LLM patch action 错误时自动重试，提高补丁生成成功率

## Phase 10.6：netns + TUN E2E 验证结果

**日期**：2026-05-10 | **环境**：Debian 12, Linux 6.1.0-45-amd64

| Transport | 默认模式 | E2E Ping | 丢包率 | RTT |
|-----------|---------|----------|--------|-----|
| TCP | PASS | PASS | 0% | 0.5–1.8ms |
| WebSocket | PASS | PASS | 0% | 2.5–4.6ms |

**关键发现**：
- 共享 `--session-id` 机制工作正常 — E2E ping 期间无 "Dropping frame" 错误
- 不需要 Core 更改 — ServerCore/ClientCore 已支持显式 session ID
- Session ID 隔离得以保留 — 不匹配丢弃逻辑未被修改
- WebSocket 比原始 TCP 增加约 2–3ms 开销（由于 framing 和 async event loop）

## 未来扩展

- Phase 10.7：稳定性与性能 benchmark（长时间运行、吞吐量对比、资源占用评估）
- SSHTransport 服务端侧完善
- Core variant 扩展：strict_session、alt_frame_codec、experimental_forwarding
- 故障反馈循环（验证失败时重试，最多 3 次）
- Session 审计日志
