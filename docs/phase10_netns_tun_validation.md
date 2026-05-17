# Phase 10.3/10.4：netns + TUN 验证 — Transport/Core 替换

## 目标

本阶段为 Transport/Core 替换建立了 **Linux netns + 真实 TUN 验证 Gate**。它是 LLM Agent 替换管线中的**第二和第三验证 Gate**：

| Gate | 内容 | 环境 | 自动化程度 |
|------|------|------|------------|
| **Gate 1** | Smoke matrix（`smoke_replacement_matrix.py`） | CI、开发者笔记本 | 全自动 |
| **Gate 2** | netns + TUN 验证（`phase10_netns_tun_validation.sh`） | 开发者工作站（root） | 半自动 |
| **Gate 3** | 完整多机集成 | 实验环境 | 手动 |

Gate 1 回答："替换后的系统能否通过基本的收发测试？"  
Gate 2 回答："替换后的系统能否使用真实内核 TUN 设备和 IP 包正常工作？"  
Gate 3 回答："替换后的系统能否在真实网络条件下正常工作？"

Gate 2 有两种模式：

| 模式 | 参数 | 验证内容 |
|------|------|----------|
| **默认**（Phase 10.3） | （无） | 环境、TUN 创建、underlay 连通性、进程健康 |
| **E2E Ping**（Phase 10.4） | `--e2e-ping` | 通过 TUN 隧道进行真实 IP 包转发的 ping 验证 |

## 何时运行 Gate 2

在以下条件满足后运行此验证：

1. 本地 smoke matrix（Gate 1）通过
2. LLM Agent 已应用 Transport 或 Core 替换补丁
3. `git apply --check` 和应用后验证通过

**不要**在 CI 中运行 — 这需要 root 权限和内核 TUN 支持。

## 拓扑结构

```
┌─────────────────────────────────────────────────────────────────┐
│                         宿主机                                    │
│                                                                  │
│   ┌─────────────────────────┐   ┌─────────────────────────┐    │
│   │  vpn_srv_validation     │   │  vpn_cli_validation     │    │
│   │  （命名空间）            │   │  （命名空间）            │    │
│   │                         │   │                         │    │
│   │  tun0                   │   │  tun1                   │    │
│   │  10.8.0.1/24            │   │  10.8.0.2/24            │    │
│   │                         │   │                         │    │
│   │  veth_srv               │   │  veth_cli               │    │
│   │  192.168.200.1/24       │◄──┼── veth pair ───────────►│    │
│   │                         │   │  192.168.200.2/24       │    │
│   └─────────────────────────┘   └─────────────────────────┘    │
│            │                              │                     │
│            │  Transport 隧道              │                     │
│            │  （tcp:2222 或 ws:port）      │                     │
│            └──────────────────────────────┘                     │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 地址分配

| 接口 | 命名空间 | IP | 用途 |
|------|---------|-----|------|
| tun0 | vpn_srv_validation | 10.8.0.1/24 | 服务端 TUN 端点 |
| tun1 | vpn_cli_validation | 10.8.0.2/24 | 客户端 TUN 端点 |
| veth_srv | vpn_srv_validation | 192.168.200.1/24 | Underlay 连通性 |
| veth_cli | vpn_cli_validation | 192.168.200.2/24 | Underlay 连通性 |

> **关于子网选择的说明**：本脚本使用 `192.168.200.0/24` 作为 veth（Phase 10.3），而 Phase 3 使用 `192.168.100.0/24`。不同的子网避免了 Phase 3 和 Phase 10.3 命名空间同时存在时的冲突。

## 前置条件

- **OS**：Linux（必需 — 使用网络命名空间和 `/dev/net/tun`）
- **权限**：root 或 `CAP_NET_ADMIN`
- **内核**：`/dev/net/tun`、网络命名空间支持、veth pairs
- **工具**：`iproute2`（`ip`）、`python3`
- **项目**：依赖已安装（`pip install -r requirements.txt`）

如果前置条件不满足，脚本会优雅跳过（exit 0）。

## 用法

### 基本用法

```bash
cd /data/xjr/VPN-LLM
sudo ./scripts/phase10_netns_tun_validation.sh
```

### 指定传输协议

```bash
sudo ./scripts/phase10_netns_tun_validation.sh --transport websocket
```

### 保留环境用于手动测试

```bash
sudo ./scripts/phase10_netns_tun_validation.sh --transport tcp --keep --verbose
```

使用 `--keep` 后，命名空间和 TUN 设备会被保留。手动清理：

```bash
sudo ip netns delete vpn_cli_validation
sudo ip netns delete vpn_srv_validation
```

### CLI 选项

| 选项 | 默认值 | 说明 |
|------|--------|------|
| `--transport TYPE` | `tcp` | 要验证的传输协议：`tcp` 或 `websocket` |
| `--timeout SECONDS` | `15` | 等待服务端/客户端启动的最长时间 |
| `--keep` | false | 验证后保留命名空间 |
| `--verbose` | false | 打印详细状态和路由表 |
| `--preflight-only` | false | 仅运行前置检查后退出。不创建命名空间、TUN 设备或进程。CI 安全 |
| `--e2e-ping` | false | 启用端到端 TUN ping 验证（Phase 10.4） |
| `--ping-count N` | `2` | ping 包数量（仅配合 `--e2e-ping`） |
| `--ping-timeout SEC` | `5` | ping 超时秒数（仅配合 `--e2e-ping`） |
| `--tcpdump` | false | 启用数据包捕获用于诊断（仅配合 `--e2e-ping`） |
| `-h`、`--help` | — | 显示帮助 |

### CI 和轻量检查

对于 CI 管线或快速环境检查，使用 `--preflight-only`：

```bash
# CI 安全 — exit 0，不创建命名空间，不需要 root
bash scripts/phase10_netns_tun_validation.sh --preflight-only
```

此模式：
- 运行所有前置检查，包括真实的 netns 能力探测
- 如果环境无法创建网络命名空间，则 exit 0 并 SKIP
- 如果所有前置条件满足，则 exit 0 并 PASS
- 从不创建命名空间、TUN 设备或服务端/客户端进程
- 从不要求 root（但如果以 root 运行也能正确处理）

## 脚本验证内容

脚本按顺序执行以下检查：

1. **前置检查**：Linux OS、`ip`、`python3`、`/dev/net/tun`、root/CAP_NET_ADMIN、真实 netns 能力探测 — 缺失则跳过
2. **设置**：创建 `vpn_srv_validation` 和 `vpn_cli_validation` 命名空间、veth pair、分配 IP
3. **Underlay 连通性**：从客户端命名空间 ping 服务端命名空间（通过 veth）
4. **服务端启动**：使用真实 TUN（**不使用** `--mock-tun`）启动 `src/server.py`
5. **客户端启动**：使用真实 TUN 启动 `src/client.py`，验证隧道建立
6. **TUN 设备检查**：验证 tun0 存在于服务端命名空间，tun1 存在于客户端命名空间
7. **TUN IP 配置**：分配 `10.8.0.1/24` 给 tun0，`10.8.0.2/24` 给 tun1
8. **清理**（除非 `--keep`）：终止进程，删除命名空间

## 脚本不验证的内容

- **TUN 到 TUN ping（不使用 --e2e-ping）**：默认模式不运行 `ping -I tun1 10.8.0.1`。使用 `--e2e-ping`（Phase 10.4）进行自动化双向 TUN ping 验证
- **长时间稳定性**：脚本仅检查启动。如需长时间测试，使用 `--keep` 并运行手动测试
- **数据完整性（不使用 --e2e-ping）**：默认模式检查隧道进程启动和 TUN 设备出现。使用 `--e2e-ping` 进行自动化 IP 包转发验证

## 手动真实 IP 包验证

使用 `--keep` 成功执行脚本后，验证真实 IP 包流：

### 终端 1：在服务端 TUN 上抓包

```bash
sudo ip netns exec vpn_srv_validation tcpdump -i tun0 -n icmp
```

### 终端 2：在客户端 TUN 上抓包

```bash
sudo ip netns exec vpn_cli_validation tcpdump -i tun1 -n icmp
```

### 终端 3：在 veth 上抓包（隧道流量）

```bash
sudo ip netns exec vpn_srv_validation tcpdump -i veth_srv -n
```

### 终端 4：通过隧道 ping

```bash
sudo ip netns exec vpn_cli_validation ping -I tun1 10.8.0.1
```

### 预期结果

1. **客户端 tun1 tcpdump**：ICMP echo request 发出
2. **客户端 veth_cli**（如果监控）：携带封装数据的 TCP/WebSocket 帧
3. **服务端 veth_srv**（如果监控）：TCP/WebSocket 帧到达
4. **服务端 tun0 tcpdump**：ICMP echo request 进入，ICMP echo reply 发出
5. **客户端 tun1 tcpdump**：ICMP echo reply 进入
6. **ping 输出**：带 RTT 的回复

## 退出码

| 码 | 含义 |
|----|------|
| 0 | 所有检查通过，或前置条件不满足（skip） |
| 1 | 验证失败（基础设施或进程错误） |

## 常见问题

| 症状 | 可能原因 | 检查 |
|------|----------|------|
| ping 无输出 | 内核本地传递到 TUN IP | 客户端 ns 中 `ip route get 10.8.0.1` |
| ping: Network is unreachable | 没有到 TUN 子网的路由 | 客户端 ns 中 `ip route` |
| 服务端启动时异常退出 | 端口已被占用 | 服务端 ns 中 `ss -tlnp` |
| 没有 TUN 设备 | 进程在打开 TUN 之前异常退出 | 检查 server/client 日志 |

## E2E Ping 模式（Phase 10.4）

### 启用 E2E ping

```bash
# 使用 TCP 进行基本 E2E ping
sudo ./scripts/phase10_netns_tun_validation.sh --transport tcp --e2e-ping

# 详细输出和数据包捕获
sudo ./scripts/phase10_netns_tun_validation.sh --transport websocket --e2e-ping --verbose --tcpdump

# 自定义 ping 参数
sudo ./scripts/phase10_netns_tun_validation.sh --transport tcp --e2e-ping --ping-count 5 --ping-timeout 3
```

### E2E ping 验证内容

`--e2e-ping` 模式在默认模式基础上增加以下检查：

1. **共享 session ID**：脚本自动向服务端和客户端传递固定测试 `--session-id`，确保它们共享相同的会话标识符并接受彼此的帧
2. **显式 TUN 路由**：配置点对点 TUN IP（`peer /32` 记法）并添加显式路由（`ip route add 10.8.0.x dev tunX`）
3. **客户端 → 服务端 ping**：`ping -I tun1 -c $PING_COUNT -W $PING_TIMEOUT 10.8.0.1`
4. **服务端 → 客户端 ping**：`ping -I tun0 -c $PING_COUNT -W $PING_TIMEOUT 10.8.0.2`

### ping 成功的含义

如果 ping 成功：
- Transport 隧道正在承载真实 IP 包
- ClientCore 正确从 tun1 读取 ICMP 并发送 DATA 帧
- ServerCore 正确接收 DATA 帧并将 ICMP 写入 tun0
- 内核通过 tun0 → ServerCore → Transport → ClientCore → tun1 将 ICMP 回复路由回来
- **这确认了通过替换后的 Transport/Core 的端到端 IP 包转发**

### ping 失败的原因

ping 失败**不一定**意味着 Transport/Core 替换有问题。常见原因：

| 原因 | 诊断信号 |
|------|----------|
| **Session ID 不匹配** | `grep "Dropping frame" server.log` 显示丢弃。脚本传递共享 `--session-id`，所以这通常意味着手动调用时 ID 不匹配 |
| **内核本地传递** | 客户端 ns 中 `ip route get 10.8.0.1` 显示 `local` |
| **TUN 路由缺失** | `ip route get 10.8.0.1` 显示没有路由或设备错误 |
| **Transport 隧道断开** | Underlay veth ping 会先失败 |
| **TUN fd 未读取** | DEBUG 服务端/客户端日志中没有 `TUN->Transport READ` |

### Session ID 共享

`ServerCore` 和 `ClientCore` 各自验证传入帧是否携带自己的 `session_id`。当服务端和客户端生成独立的 session ID（默认行为）时，每个帧都会被接收端丢弃 — 包括 TUN ping 包。

**共享 session ID**：`--session-id HEX` CLI 参数和 `session_id` 配置字段允许两端使用相同的 16 字节会话标识符：

```bash
# 通过 CLI（优先级：CLI > 配置 > 随机）
python3 -m src.server --config config/server_netns.yaml --session-id 00112233445566778899aabbccddeeff
python3 -m src.client --config config/client_netns.yaml --session-id 00112233445566778899aabbccddeeff

# 通过配置
session_id: "00112233445566778899aabbccddeeff"
```

**优先级**：CLI `--session-id` > 配置 `session_id` > 自动生成（随机）。

netns 验证脚本（`phase10_netns_tun_validation.sh`）自动向服务端和客户端传递固定测试 `--session-id`，使得在共享 session 隔离下 E2E ping 正常工作。

> **注意**：`session_id` 是会话隔离标识符，不是认证密钥或加密密钥。在生产环境中，session ID 应由控制平面协商或分发。

### 默认模式 vs E2E ping 模式的主要区别

| 方面 | 默认模式 | `--e2e-ping` 模式 |
|------|----------|-------------------|
| 环境设置 | 是 | 是 |
| Underlay veth ping | 是 | 是 |
| TUN 创建检查 | 是 | 是 |
| 共享 session ID | 否 | 是（自动通过 `--session-id` 传递） |
| TUN IP 配置 | `/24` 子网 | `peer /32` 点对点 |
| 进程健康检查 | 是 | 是 |
| 显式 TUN 路由 | 否 | 是 |
| 通过隧道真实 IP ping | 否 | 是（双向） |
| tcpdump 捕获 | 否 | 可选（`--tcpdump`） |
| 失败诊断 | 基本 | 完整（ip addr、ip route、日志、pcaps） |

### tcpdump 辅助诊断

当启用 `--tcpdump` 时，会创建三个数据包捕获文件：

```
/tmp/vpn_validation_pcap.XXXXXX/
├── server_tun0.pcap      # 服务端 TUN 上的 ICMP
├── client_tun1.pcap      # 客户端 TUN 上的 ICMP
└── server_veth_srv.pcap  # underlay 上的隧道流量
```

检查方法：
```bash
tcpdump -r /tmp/vpn_validation_pcap.XXXXXX/server_tun0.pcap -n
tcpdump -r /tmp/vpn_validation_pcap.XXXXXX/server_veth_srv.pcap -n
tcpdump -r /tmp/vpn_validation_pcap.XXXXXX/client_tun1.pcap -n
```

**成功预期**：
- `server_veth_srv.pcap`：携带封装 DATA 的 TCP/WebSocket 帧
- `server_tun0.pcap`：ICMP echo request（入）和 echo reply（出）
- `client_tun1.pcap`：ICMP echo request（出）和 echo reply（入）

**Session ID 不匹配预期**：
- `server_veth_srv.pcap`：TCP/WebSocket 帧可见（transport 正常）
- `server_tun0.pcap`：空（服务端丢弃帧，不写入 tun0）
- `client_tun1.pcap`：仅 ICMP echo request（出），无 reply（入）

## 与 LLM Agent 工作流的集成

netns 验证是 LLM Agent 替换管线中的手动步骤：

```
LLM 生成补丁
  → git apply --check
  → 人工确认 --apply-patch
  → 应用后验证
  → --run-replacement-smoke（Gate 1：自动化，CI 安全）
  → phase10_netns_tun_validation.sh（Gate 2a：默认模式，需 root）
  → phase10_netns_tun_validation.sh --e2e-ping（Gate 2b：E2E ping，需 root）
  → 提交建议
```

Gate 2a（默认模式）验证基础设施：命名空间、TUN 设备、underlay 连通性和进程健康。Gate 2b（e2e-ping）增加真实 IP 包转发验证。两者都需要显式人工调用 — 脚本**不会**被 LLM Agent 自动调用，因为 root 权限和内核 TUN 支持在 CI 或典型开发者环境中需要显式设置。

## Phase 10.6：真实 E2E 结果

### 环境

- **OS**：Debian 12（Linux 6.1.0-45-amd64）
- **内核**：x86_64，`/dev/net/tun` 存在，`iproute2` 6.1.0
- **Python**：3.11.2（系统），websockets 10.4（系统）/ 16.0（pip 用户）
- **日期**：2026-05-10

### 结果

| Transport | 默认模式 | E2E Ping（P2P） | 备注 |
|-----------|---------|-----------------|------|
| TCP | PASS | **PASS** | 0% 丢包，0.5–1.8ms RTT |
| WebSocket | PASS | **PASS** | 0% 丢包，2.5–4.6ms RTT |

### 使用的命令

```bash
# 默认模式
sudo bash scripts/phase10_netns_tun_validation.sh --transport tcp --verbose
sudo bash scripts/phase10_netns_tun_validation.sh --transport websocket --verbose

# E2E ping
sudo bash scripts/phase10_netns_tun_validation.sh --transport tcp --verbose --e2e-ping
sudo bash scripts/phase10_netns_tun_validation.sh --transport websocket --verbose --e2e-ping
```

### Phase 10.6 发现并修复的 Bug

1. **客户端配置子网不匹配**（[config/client_netns.yaml](../config/client_netns.yaml)）：`host: 192.168.100.1`（Phase 3 子网）→ 修复为 `192.168.200.1` 以匹配 Phase 10.3 使用的 veth 子网（`192.168.200.0/24`）

2. **WebSocket transport v10/v16 不兼容**（[src/transport/websocket_transport.py](../src/transport/websocket_transport.py)）：
   - `from websockets.asyncio.client import connect` → `from websockets import connect`
   - `from websockets.asyncio.server import serve` → `from websockets import serve`
   - `websockets.asyncio` 子包在 v16 中被移除，在系统安装的 v10.4 中从未存在过。顶层 `connect`/`serve` 在所有版本中均可使用
   - `additional_headers`（v16）vs `extra_headers`（v10）参数名差异通过在导入时检测 websockets 主版本号来处理

### 关键发现

- **共享 session_id 正常工作**：Phase 10.5 的 `--session-id` 机制使 E2E ping 无任何 "Dropping frame" 错误
- **不需要 Core 更改**：ServerCore 和 ClientCore 已支持显式 session ID。仅入口点和传输层需要修复
- **Session ID 隔离得以保留**：不匹配丢弃逻辑（`_handle_frame`）未被修改
- **WebSocket 延迟较高**：WebSocket 比原始 TCP 增加约 2–3ms 开销，由于 framing 和 async event loop

## 相关文件

| 文件 | 用途 |
|------|------|
| `scripts/phase10_netns_tun_validation.sh` | 主验证脚本 |
| `docs/phase10_netns_tun_validation.md` | 本文档 |
| `config/server_netns.yaml` | 服务端配置（脚本使用） |
| `config/client_netns.yaml` | 客户端配置（脚本使用） |
