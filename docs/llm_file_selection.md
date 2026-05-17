# LLM File Selection — 本地索引驱动的文件选择

## 为什么需要本地文件选择？

LLM (尤其是通用大模型) 在生成 patch 时会 "猜" 需要修改哪些文件。这种猜测不鲁棒：
- LLM 不了解仓库的实际文件结构
- LLM 可能输出不存在的文件路径
- LLM 可能遗漏关键依赖文件
- 对于大型仓库，LLM 无法枚举所有相关文件

**解决方案**: 文件选择完全在本地完成，LLM 只负责在已选定的文件范围内生成修改。

## 文件选择管线

### Phase 1: RepoIndexer — 仓库扫描

```
RepoIndexer(root_dir).build() → RepoIndex
```

- 递归扫描仓库目录
- 忽略 .git、__pycache__、.llm_tasks、venv 等目录
- 忽略 .env、.key、.pem 等敏感文件
- 对 Python 文件使用 `ast` 提取 class/function/import
- 对 YAML/JSON 提取顶层 config keys
- 识别 test files (test_*.py, *_test.py)
- 识别 doc files (README.md, docs/*.md)
- 输出可序列化的 `RepoIndex`

### Phase 2: FileRetriever — 多路召回

```
FileRetriever(repo_index).retrieve(request, plan) → CandidateFile[]
```

六路召回策略（按优先级排序）:

| # | 策略 | 权重 | 说明 |
|---|---|---|---|
| 1 | 关键词匹配 | 0.90 | 请求中的关键词匹配文件路径 |
| 2 | Symbol 匹配 | 0.85 | 关键词匹配 Python class/function/import 名 |
| 3 | Config key 匹配 | 0.80 | 关键词匹配 YAML/JSON 顶层 key |
| 4 | Task type 规则 | 0.70 | 根据 task_type 召回预定义路径模式 |
| 5 | Test 文件映射 | 0.50 | 根据 task_type 映射相关测试文件 |
| 6 | Doc 文件映射 | 0.40 | 根据 task_type 映射相关文档文件 |

额外: LLM planner hints (candidate_files) 以 0.60 分数合并，若文件存在则标记为 edit action。

### Phase 3: ImpactExpander — 影响面扩展

```
ImpactExpander(repo_index).expand(request, plan, candidates) → FileSelection
```

扩展规则:

| 区域 | must_edit | must_review | allowed_create |
|---|---|---|---|
| transport | src/transport/__init__.py, base.py, factory.py, config.py | src/transport/*, config/*.yaml | src/transport/ |
| core | src/core/__init__.py, client_core.py, server_core.py, frame.py, session.py | src/core/*, src/common/*, src/tun/* | src/core/, src/common/ |
| llm_agent | src/llm/__init__.py, scripts/llm_task.py | src/llm/*, scripts/llm_task.py | src/llm/ |
| validation | validation_runner.py, replacement_validator.py, smoke script | src/llm/*, scripts/* | src/llm/, scripts/ |
| mixed_feature | — | src/*, tests/* | src/transport/, src/core/, src/common/, src/llm/, src/tun/, tests/, docs/, scripts/, config/ |

LLM planner hints (candidate_files) 的处理:
- **文件存在且评分 ≥0.9** → 可进入 must_edit
- **文件存在但评分 <0.9** → 进入 must_review（不能直接进入 must_edit）
- **文件不存在** → 记录为 rejected_hints

> Phase 11.2 引入了候选文件评分阈值（0.9），低于阈值的候选文件不能进入 must_edit。

### Phase 4: ContextBuilder — 上下文构建

```
ContextBuilder(root_dir).build(file_selection) → (repo_context, ContextSummary)
```

- 读取 must_edit_files 和 must_review_files
- 每个文件最多 12KB（防止 token 溢出）
- 附带 candidates 详情、allowed paths、rejected hints
- 附带 test_files 和 doc_files 路径列表

### Phase 5: PatchGenerator — 受约束的 Patch 生成

```
PatchGenerator.generate(request, plan, repo_context,
                        allowed_edit_files, allowed_create_paths) → patch.diff
```

约束:
1. **FILE 必须在 allowed_edit_files 中** — 否则拒绝（除非 ACTION: create）
2. **ACTION: create 必须在 allowed_create_paths 下** — 否则拒绝
3. **FIND 必须恰好出现 1 次** — 0 次或 >1 次均拒绝
4. **敏感路径扫描** — .env、.git、.key、.pem 等永远拒绝
5. **Secret scanning** — private key、API key、password 等模式扫描

## 多元化修改需求支持

| 需求类型 | 任务类型 | 文件选择行为 |
|---|---|---|
| 替换外层 transport | transport_change | 召回 src/transport/*、config/*.yaml、tests/test_*transport*.py、README、docs |
| 修改内层 core | core_change | 召回 src/core/*、src/common/frame.py、src/tun/*、tests/test_core.py |
| 新增 transport | transport_change + "new/add" | 同上 + allowed_create_paths 含 src/transport/ |
| 新增 core 功能 | core_change + "new/add" | 同上 + allowed_create_paths 含 src/core/ |
| 新增 validation gate | validation + "new/add" | 召回 scripts/*、src/llm/*validation*、tests/test_*validation* |
| 修改 LLM workflow | llm_agent | 召回 scripts/llm_task.py、src/llm/*、tests/test_llm_* |
| 修改测试 | test_addition | 召回 tests/*、允许在 tests/ 下创建 |
| 修改配置 | config_change | 召回 config/*、src/common/config.py |
| 修改文档 | docs_update | 召回 docs/*、README.md |
| 混合新功能 | mixed_feature | 广范围召回，多目录 create 权限 |

## LLM 猜文件问题的解决状态

**已解决。** LLM 不再负责猜测文件。

- LLM 输出的 `candidate_files` 降级为 hints（仅作补充）
- 最终文件选择由 RepoIndexer + FileRetriever + ImpactExpander 本地完成
- LLM 的 patch 生成受 `allowed_edit_files` / `allowed_create_paths` 硬约束
- 不存在的 LLM hint 被记录为 `rejected_hints`，不导致失败
