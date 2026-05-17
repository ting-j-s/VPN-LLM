# Phase 10.1：Transport/Core 替换 Smoke 验证矩阵

## 目标

本阶段构建了一个**统一的 smoke 验证矩阵**，用于验证 Transport 和 Core 替换的最小可运行性。它是 LLM 驱动协议和内核替换的验证入口。

当 LLM Agent 生成或修改 Transport 或 Core 实现时，系统可以调用此矩阵来回答一个问题：

> **"替换后的系统是否仍通过最小 smoke 检查？"**

这*不是*普通的传输测试 — 它是为 LLM Agent 工作流设计的**替换验证 Gate**。

## 动机

VPN 项目有两个可替换维度：

| 维度 | 可变更内容 | 示例 |
|------|-----------|------|
| **Transport** | 外层协议（线格式） | TCP → WebSocket、添加 HTTP/2、添加 QUIC |
| **Core** | VPN 内核（会话、转发、帧） | Default → strict-session、alt-frame-codec |

当 LLM Agent 在任一维度提出替换时，我们需要快速、确定性地回答："这个替换是否最小可运行？"

## 在 LLM 驱动替换中的角色

Smoke matrix 是 LLM 生成的 Transport 或 Core 补丁之后的**第一道运行时 Gate**。

### 何时运行

- 在 `--apply-patch` 和应用后验证通过之后
- 仅在显式传递 `--run-replacement-smoke` 时（选择加入）
- `ReplacementValidator`（`src/llm/replacement_validator.py`）编排运行

### Transport 选择逻辑

测试的 transports 取决于任务类型和目标 transport：

| 任务条件 | 选择的 Transports |
|----------|-------------------|
| `target_transport` = websocket | mock, websocket |
| `target_transport` = tcp | mock, tcp |
| `target_transport` = tls | mock, tls |
| `task_type` = core_change | mock, tcp, tls, websocket（广泛矩阵 — Core 影响所有 transports） |
| `task_type` = refactor / bugfix / unknown | mock, tcp, tls, websocket |
| 所有其他类型 | mock, tcp, websocket（默认） |

### JSON 输出供 Agent 消费

`--json` 参数产生可供 `ReplacementValidator` 消费的结构化输出：

```json
{
  "results": [{"transport": "websocket", "core": "default", "status": "pass", ...}],
  "summary": {"passed": 3, "failed": 0, "skipped": 0}
}
```

Agent 解析此输出来生成"Replacement Smoke Validation"报告部分，并将 `replacement_validation.json` 保存到任务目录。

### Smoke matrix 的边界

Smoke matrix **不**做以下事情：
- 使用真实 TUN 设备（使用 MockTunDevice）
- 在真实网络命名空间中运行（不需要 root）
- 测量性能或稳定性
- 替代 netns/TUN E2E 验证（Gate 3/4）
- 替代 benchmark 测试（Gate 5）

它有意保持轻量 — 在进入更重的验证 Gate 之前进行快速的"还能用吗？"检查。

## Transport 维度

| Transport | Smoke 检查 | CI 状态 |
|-----------|-----------|---------|
| `mock` | 进程内 MockTransport 注入/接收 | 稳定 |
| `tcp` | localhost 客户端/服务端 发送/接收 | 稳定 |
| `tls` | localhost 客户端/服务端，临时证书 | 稳定 |
| `websocket` | localhost 客户端/服务端 发送/接收 | 稳定 |
| `ssh` | **始终跳过** — 需要外部 SSH 服务器 | N/A |

### 为什么 SSH 默认跳过

SSH transport 需要目标主机上运行 SSH 服务器（`sshd`）和有效凭据。这在 CI 或本地 smoke 运行中不可用。矩阵将 SSH 标记为 `skip`，消息为 `requires external SSH server`。

使用 `--include-ssh` 在有 SSH 服务器可用时显式运行 SSH smoke。

### TLS 临时证书

TLS smoke 在 `tempfile.mkdtemp()` 目录中生成自签名证书。这些证书在检查后立即清理。不持久化或提交任何证书。

## Core 维度

### `default`（当前）

默认 Core smoke 验证：

1. `ClientCore` 和 `ServerCore` 可导入
2. `session_id` 验证正常工作 — 携带错误 `session_id` 的帧被丢弃
3. Core 启动/停止周期无错误完成

它使用 `MockTransport` 和 `MockTunDevice` — 不创建真实 TUN 设备。

### 扩展接口

每个 Core 通过 `CORE_SMOKE_REGISTRY` 注册：

```python
_CORE_SMOKE_REGISTRY = {
    "default": _smoke_core_default,
    # 未来条目：
    # "strict_session": _smoke_core_strict_session,
    # "alt_frame_codec": _smoke_core_alt_frame,
    # "experimental_forwarding": _smoke_core_experimental,
}
```

添加新 Core smoke：
1. 实现签名为 `(transport_result: SmokeResult) -> SmokeResult` 的处理函数
2. 将其注册到 `CORE_SMOKE_REGISTRY`
3. 运行：`--cores default,new_core`

### 为什么不使用真实 TUN

真实 TUN 设备需要 `root` 权限和内核 `tun` 模块支持。Smoke matrix 面向没有 root 权限的 CI 和开发者笔记本。Core 逻辑（session_id 验证、启动/停止、数据路径）通过 `MockTunDevice` 验证。

## 用法

### 命令行

```bash
# 默认：mock, tcp, tls, websocket 与 default core
python3 scripts/smoke_replacement_matrix.py

# 指定 transports
python3 scripts/smoke_replacement_matrix.py --transports mock,tcp,websocket --cores default

# 包含 SSH（当 sshd 可用时）
python3 scripts/smoke_replacement_matrix.py --transports tcp,ssh --cores default --include-ssh

# JSON 输出（供 LLM Agent 消费）
python3 scripts/smoke_replacement_matrix.py --transports mock,tcp,tls,websocket --cores default --json
```

### 文本输出

```
Transport/Core Smoke Matrix
============================================================
mock/default       PASS
tcp/default        PASS     0.00s
tls/default        PASS     0.03s
websocket/default  PASS     0.06s
ssh/default        SKIP     (requires external SSH server)
```

### JSON 输出

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

### 退出码

| 码 | 含义 |
|----|------|
| 0 | 所有非跳过条目通过（或仅跳过） |
| 1 | 至少一个条目失败 |

## 理解 pass / fail / skip

| 状态 | 含义 |
|------|------|
| `pass` | 最小发送/接收往返成功。Transport 可运行 |
| `fail` | Smoke 检查抛出异常或断言错误。error 字段包含详细信息 |
| `skip` | 前置条件不满足（例如 SSH 需要外部服务器）。不是失败 |

## 与 LLM Agent 的集成

Smoke matrix 通过 `ReplacementValidator`（`src/llm/replacement_validator.py`）集成到 LLM Agent 应用后验证管线中。

### 显式调用

替换 smoke **仅**在显式请求时运行：

```bash
python3 scripts/llm_task.py \
    --request "switch transport to websocket" \
    --use-llm-planner \
    --generate-patch --apply-patch \
    --run-replacement-smoke
```

不使用 `--run-replacement-smoke` 则不会执行矩阵 — 它是选择加入的。

### Transport 选择规则

`ReplacementValidator` 根据任务计划自动选择 transports：

| 任务条件 | 选择的 Transports |
|----------|-------------------|
| `target_transport` = websocket | mock, websocket |
| `target_transport` = tcp | mock, tcp |
| `target_transport` = tls | mock, tls |
| `target_transport` = ssh | mock, ssh |
| `task_type` = core_change | mock, tcp, tls, websocket |
| `task_type` = refactor | mock, tcp, tls, websocket |
| `task_type` = bugfix | mock, tcp, tls, websocket |
| `task_type` = unknown | mock, tcp, tls, websocket |
| 所有其他类型 | mock, tcp, websocket（默认） |

使用 `--include-tls-smoke` 或 `--include-ssh-smoke` 强制包含 TLS/SSH，无论任务计划如何。

### Agent 工作流

1. Agent 提出 Transport 或 Core 补丁
2. `git apply --check` 通过
3. 人工确认 `--apply-patch`
4. 应用后验证通过
5. **`--run-replacement-smoke`** 触发 `ReplacementValidator`：
   - 根据 `target_transport` 和 `task_type` 选择 transports
   - 运行 `smoke_replacement_matrix.py --json`
   - 解析 JSON 输出
   - 报告每个 transport 的 pass/fail/skip
6. 结果保存到 `.llm_tasks/<task_id>/replacement_validation.json`
7. 结果显示在报告的"Replacement Smoke Validation"部分
8. 如果任何 smoke 失败，报告声明："在替换 smoke 失败修复之前不要提交"

## 未来扩展

### 新 Transport

| Transport | 需要做的 |
|-----------|---------|
| HTTP/2 | 实现 `src/transport/http2_transport.py`，添加 `_smoke_http2()`，注册到 `_TRANSPORT_SMOKE` |
| QUIC | 实现 `src/transport/quic_transport.py`，添加 `_smoke_quic()`，注册 |
| gRPC | 实现 `src/transport/grpc_transport.py`，添加 `_smoke_grpc()`，注册 |

### 新 Core

| Core | 需要做的 |
|------|---------|
| `strict_session` | 在 Core 中实现更严格的 session 验证，注册 smoke handler |
| `alt_frame_codec` | 实现替代 Frame 编码，注册 smoke handler |
| `experimental_forwarding` | 实现实验性 NAT/路由策略，注册 smoke handler |

### CI 集成

Smoke matrix 可以添加到 `.github/workflows/tests.yml`：

```yaml
- name: Smoke replacement matrix
  run: python3 scripts/smoke_replacement_matrix.py --transports mock,tcp,websocket --cores default --json
```

TLS 需要 CI 环境中安装 `openssl`（通常预装）。WebSocket 需要 `websockets` Python 包。

## 相关文件

| 文件 | 用途 |
|------|------|
| `scripts/smoke_replacement_matrix.py` | 主脚本 |
| `tests/test_smoke_replacement_matrix.py` | 测试（29 tests） |
| `docs/phase10_replacement_smoke_matrix.md` | 本文档 |
