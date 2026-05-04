# VPN Tunnel - 模块化 VPN 原型系统

一个用于研究和教学的模块化 VPN 隧道系统，支持多种传输层协议（SSH、TCP、TLS、WebSocket）。

> **⚠️ 安全边界说明**：本项目仅用于授权实验和教学研究。不得用于未授权网络访问、规避网络审计或隐藏流量。任何此类使用均不在本项目许可范围内。

---

## 1. 项目简介

本项目实现了一个模块化的 VPN 隧道原型系统，核心设计目标是：

- **传输层无关**：通过抽象 Transport 接口，支持多种传输协议
- **帧格式统一**：使用统一的 Frame 格式封装 IP 数据包
- **双向转发**：支持 TUN 设备与远程端之间的双向数据流
- **会话管理**：内置心跳机制检测连接健康状态
- **统计监控**：提供线程安全的流量统计

适用于网络协议学习、VPN 架构研究、安全教育等场景。

## 2. 系统架构

```
┌─────────────────────────────────────────────────────────┐
│                      User Space                          │
│                                                          │
│  ┌─────────────┐                    ┌─────────────┐    │
│  │   Client    │◄────── Tunnel ──────►│   Server    │    │
│  │   Core      │                      │   Core      │    │
│  └──────┬──────┘                      └──────┬──────┘    │
│         │                                    │           │
│  ┌──────▼──────┐                      ┌──────▼──────┐    │
│  │  Transport  │                      │  Transport  │    │
│  │  Abstraction│                      │  Abstraction│    │
│  └──────┬──────┘                      └──────┬──────┘    │
│         │                                    │           │
│  ┌──────▼──────┐                      ┌──────▼──────┐    │
│  │ SSH/TCP/TLS │◄───── Wire ─────────►│ SSH/TCP/TLS │    │
│  │ /WebSocket  │                      │ /WebSocket  │    │
│  └─────────────┘                      └─────────────┘    │
│                                                          │
└─────────────────────────────────────────────────────────┘
         │                                    │
  ┌──────▼──────────────────────────────────────▼──────┐
  │                   TUN Device                       │
  │        (MockTunDevice / LinuxTunDevice)             │
  └─────────────────────────────────────────────────────┘
                           │
                    ┌──────▼──────┐
                    │  Physical   │
                    │  Network    │
                    └─────────────┘
```

### 数据流

1. **客户端**：本地 TUN 设备接收 IP 数据包 → Frame 封装 → Transport 发送
2. **服务端**：Transport 接收 → Frame 解封 → 写入远程 TUN 设备

## 3. 目录结构

```
vpn_tunnel/
├── config/                    # 配置文件
│   ├── client.yaml           # 默认 TCP 客户端配置
│   └── server.yaml           # 默认 TCP 服务端配置
│
├── src/
│   ├── client.py            # 客户端入口
│   ├── server.py            # 服务端入口
│   │
│   ├── common/              # 公共组件
│   │   ├── config.py        # 配置加载与验证
│   │   ├── errors.py        # 统一错误类
│   │   ├── frame.py         # Frame 编解码 (VTUNmagic + 4-byte length)
│   │   └── logger.py        # 日志工具
│   │
│   ├── transport/           # 传输层抽象
│   │   ├── base.py          # Transport 基类
│   │   ├── factory.py       # Transport 工厂
│   │   ├── ssh_transport.py # SSH 传输
│   │   ├── tcp_transport.py # TCP 传输
│   │   ├── tls_transport.py # TLS 传输
│   │   └── websocket_transport.py # WebSocket 传输
│   │
│   ├── tun/                 # TUN 设备抽象
│   │   └── tun_device.py    # MockTunDevice / LinuxTunDevice
│   │
│   ├── core/                # 核心业务逻辑
│   │   ├── client_core.py   # 客户端转发循环 + 心跳
│   │   └── server_core.py   # 服务端转发循环 + 心跳
│   │
│   ├── forwarding/          # 转发功能
│   │   ├── nat.py           # NAT 规则生成
│   │   └── route.py         # 路由规则生成
│   │
│   ├── evaluation/          # 评估工具
│   │   └── stats.py         # 流量统计
│   │
│   └── llm/                 # LLM 辅助工具
│       ├── llm_client.py    # OpenAI 风格 API 客户端
│       └── code_task_manager.py # 代码任务管理器
│
├── tests/                   # 测试套件 (pytest 运行查看实际数量)
│   ├── test_frame.py
│   ├── test_config.py
│   ├── test_tcp_transport.py
│   ├── test_tls_transport.py
│   ├── test_websocket_transport.py
│   ├── test_transport_mock.py
│   ├── test_core.py         # Core 端到端测试
│   ├── test_llm_client.py
│   └── test_code_task_manager.py
│
├── requirements.txt         # Python 依赖
└── README.md               # 本文档
```

## 4. 运行方式

### 环境准备

```bash
# 安装依赖
pip install -r requirements.txt

# 可选依赖（用于完整功能）
pip install paramiko websockets requests pyyaml
```

### 运行测试

```bash
cd /data/xjr/VPN-LLM/vpn_tunnel
python3 -m pytest tests/ -v
```

### Mock TUN 模式（无需 root）

MockTunDevice 不需要 TUN 设备权限，适合功能测试。使用 `--mock-tun` 参数启用：

```bash
# 服务端
python3 -m src.server --config config/server.yaml --transport tcp --mock-tun

# 客户端
python3 -m src.client --config config/client.yaml --transport tcp --mock-tun
```

### MockTransport 模式（仅用于单元测试）

**注意**：MockTransport 使用内存队列模拟传输，**不能**用于两个独立进程之间的通信。它仅适用于单进程内的单元测试和集成测试。

```bash
# 错误用法 - 两个独立进程无法通过 MockTransport 通信
python3 -m src.server --transport mock  # 不会生效
python3 -m src.client --transport mock  # 不会生效

# 正确用法 - 在测试代码中使用 MockTransport
# 参见 tests/test_transport_mock.py 和 tests/test_core.py
```

### TCP 模式（需要网络权限）

默认配置文件已使用 TCP 类型：

```bash
# 服务端
python3 -m src.server --config config/server.yaml --mock-tun

# 客户端
python3 -m src.client --config config/client.yaml --mock-tun
```

### TLS 模式

```bash
# 生成测试证书（仅用于实验）
openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 365 -subj "/CN=localhost"

# 服务端（需要配置 certfile, keyfile）
python3 -m src.server --config config/server.yaml --transport tls

# 客户端（需要配置 cafile 验证服务器证书，或使用 insecure_skip_verify 跳过验证）
python3 -m src.client --config config/client.yaml --transport tls
```

### WebSocket 模式

WebSocketTransport 已通过本地 localhost client/server 基础通信测试：

- ✅ 每个实例在 dedicated background asyncio event loop 中运行，不依赖调用方线程的事件循环
- ✅ 对外提供同步 `connect` / `send` / `recv` 接口，与 Transport 抽象一致
- ✅ `recv()` 超时时抛出 `TransportTimeout`，行为与 TCP/TLS transport 保持一致
- ✅ 支持 client 和 server 两种模式
- ⏳ 建议在真实网络和长时间运行场景下继续测试

```bash
# 服务端
python3 -m src.server --config config/server.yaml --transport websocket

# 客户端
python3 -m src.client --config config/client.yaml --transport websocket
```

### 测试状态说明

- **单向数据路径测试**：Client→Server 和 Server→Client 已验证通过
- **双向数据路径测试**：因 timing 敏感性问题暂时跳过（test_bidirectional_data_path pytest.skip）
- **MockTransport loopback 测试**：单进程内验证通过
- **TCP 跨进程通信**：连接建立成功，mock-tun 环境下 recv 超时会触发 WARNING（非 ERROR）

## 5. 配置文件说明

### 客户端配置 (client.yaml)

```yaml
client:
  tun_name: tun0        # TUN 设备名
  tun_ip: 10.8.0.2      # 本地 TUN IP
  tun_peer: 10.8.0.1    # 远程 TUN IP
  mtu: 1400             # MTU

server:
  host: 127.0.0.1       # 服务器地址
  port: 2222            # 服务器端口

transport:
  type: tcp             # 传输类型: ssh/tcp/tls/websocket/mock

session:
  heartbeat_interval: 10   # 心跳间隔（秒）
  reconnect: true          # 是否自动重连
  reconnect_interval: 3    # 重连间隔（秒）
```

### 服务端配置 (server.yaml)

```yaml
server:
  tun_name: tun0        # TUN 设备名
  tun_ip: 10.8.0.1      # 本地 TUN IP
  tun_peer: 10.8.0.2    # 远程 TUN IP
  mtu: 1400             # MTU
  listen_port: 2222     # 监听端口

forwarding:
  enable_nat: false     # 是否启用 NAT
  enable_route: true    # 是否启用路由

transport:
  type: tcp             # 传输类型

session:
  heartbeat_timeout: 30 # 心跳超时（秒）
```

### TLS 客户端配置扩展

```yaml
transport:
  type: tls
  certfile: /path/to/client.crt    # 客户端证书（可选）
  keyfile: /path/to/client.key     # 客户端私钥（可选）
  cafile: /path/to/ca.crt          # CA 证书，用于验证服务器
  verify_server: true             # 是否验证服务器证书（默认 true）
  insecure_skip_verify: false      # 跳过证书验证（默认 false，**生产环境勿用**）
```

### SSH 配置扩展

```yaml
transport:
  type: ssh
  auto_add_host_key: false        # 是否自动添加未知主机密钥（默认 false，**生产环境勿用**）
```

## 6. Frame 格式说明

所有传输数据使用统一 Frame 格式封装：

```
┌────────┬────────┬────────┬────────┬─────────────────┬────────────┐
│ Magic  │Version │ Type   │ Length │   Session ID    │  Payload   │
│ 4字节  │ 1字节  │ 1字节  │ 4字节  │    16字节       │  变长      │
│ "VTUN" │  0x01  │        │        │   (UUID)        │            │
└────────┴────────┴────────┴────────┴─────────────────┴────────────┘
```

- **Magic (4字节)**：固定值 `VTUN` (0x5654554E)
- **Version (1字节)**：当前为 `0x01`
- **Type (1字节)**：`0x01`=DATA, `0x02`=HEARTBEAT, `0x03`=AUTH, `0x04`=CLOSE
- **Length (4字节)**：Payload 的长度（大端序），不包括 Session ID
- **Session ID (16字节)**：UUID，用于标识会话
- **Payload (变长)**：数据负载

### 传输层封装

每个 Transport 在发送前额外添加 4 字节长度前缀：

```
┌──────────────┬─────────────┐
│ Length (4B)  │ Frame bytes │
│  big-endian  │             │
└──────────────┴─────────────┘
```

### 帧类型

| 类型值 | 名称 | 说明 |
|--------|------|------|
| 0x01 | DATA | IP 数据包 |
| 0x02 | HEARTBEAT | 心跳检测 |
| 0x03 | AUTH | 认证消息 |
| 0x04 | CLOSE | 关闭会话 |

## 7. Transport 抽象说明

所有 Transport 实现继承 `Transport` 基类：

```python
class Transport(ABC):
    """Transport abstract base class."""

    def connect(self) -> None:
        """Connect or start listening."""

    def send(self, data: bytes) -> None:
        """Send data with length prefix framing."""

    def recv(self, timeout: Optional[float] = None) -> Optional[bytes]:
        """Receive data with length prefix framing."""

    def close(self) -> None:
        """Close the connection."""

    def is_connected(self) -> bool:
        """Check connection status."""
```

### 已实现的 Transport

| 类型 | 说明 | 特性 |
|------|------|------|
| SSH | 基于 Paramiko | 加密传输，需 SSH 服务器，默认严格主机密钥验证 |
| TCP | 原始 TCP | 简单直接，无加密 |
| TLS | TLS 加密 TCP | 证书认证，默认启用服务器证书验证 |
| WebSocket | WebSocket 协议 | 可穿透防火墙，HTTP 兼容，已通过本地基础通信测试 |
| Mock | 内存模拟 | 无网络依赖，**仅用于单元测试，不能跨进程通信** |

### 工厂模式

使用工厂模式创建 Transport：

```python
from src.transport.factory import create_transport

transport = create_transport(config)
```

## 8. 如何添加新的 Transport

### 步骤 1：实现 Transport 类

创建 `src/transport/my_transport.py`：

```python
from .base import Transport
from ..common.errors import TransportError

class MyTransport(Transport):
    """My custom transport implementation."""

    MODE_CLIENT = "client"
    MODE_SERVER = "server"

    def __init__(self, mode: str = MODE_CLIENT, host: str = "127.0.0.1", port: int = 2225):
        if mode not in (self.MODE_CLIENT, self.MODE_SERVER):
            raise TransportError(f"Invalid mode: {mode}")
        self.mode = mode
        self.host = host
        self.port = port
        self._connected = False

    def connect(self) -> None:
        """Connect or start listening."""
        if self.mode == self.MODE_CLIENT:
            # Connect to server
            ...
        else:
            # Start server
            ...

    def send(self, data: bytes) -> None:
        """Send data with 4-byte length prefix."""
        if not self._connected:
            raise TransportError("Not connected")
        # Pack: 4-byte length + data
        length_prefix = struct.pack(">I", len(data))
        self._socket.sendall(length_prefix + data)

    def recv(self, timeout: float = None) -> Optional[bytes]:
        """Receive data with 4-byte length prefix."""
        if not self._connected:
            raise TransportError("Not connected")
        # Read 4-byte length, then full data
        ...

    def close(self) -> None:
        """Close connection."""
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected
```

### 步骤 2：注册到工厂

编辑 `src/transport/factory.py`：

```python
from .my_transport import MyTransport

SUPPORTED_TRANSPORTS = {"ssh", "tcp", "tls", "websocket", "mock", "my"}

# 添加到 create_transport()
elif transport_type == "my":
    return _create_my_transport(config)

# 添加工厂函数
def _create_my_transport(config):
    host = getattr(config.server, 'host', '127.0.0.1')
    port = getattr(config.server, 'port', 2225)
    return MyTransport(mode=MyTransport.MODE_CLIENT, host=host, port=port)
```

### 步骤 3：添加测试

创建 `tests/test_my_transport.py`

### 步骤 4：更新配置允许列表

编辑 `src/common/config.py`：

```python
ALLOWED_TRANSPORT_TYPES = {"ssh", "tcp", "tls", "websocket", "mock", "my"}
```

## 9. 如何运行测试

```bash
# 运行所有测试
python3 -m pytest tests/ -v

# 运行特定测试文件
python3 -m pytest tests/test_tcp_transport.py -v

# 运行特定测试类
python3 -m pytest tests/test_tcp_transport.py::TestTCPTransportRoundtrip -v

# 显示详细输出
python3 -m pytest tests/ -v -s --tb=long

# 快速失败模式
python3 -m pytest tests/ -v -x

# 生成覆盖率报告
python3 -m pytest tests/ --cov=src --cov-report=term-missing
```

## 10. Linux TUN 权限说明

### 权限要求

在 Linux 上使用真实的 TUN 设备需要 root 权限：

```bash
# 创建 TUN 设备需要 root
sudo ip tuntap add mode tun name tun0
sudo ip addr add 10.8.0.1/24 dev tun0
sudo ip link set tun0 up
sudo ip route add 10.8.0.0/24 dev tun0
```

### 替代方案

| 方案 | 权限需求 | 适用场景 |
|------|----------|----------|
| MockTunDevice (--mock-tun) | 无需 root | 功能测试、开发 |
| LinuxTunDevice (真实 TUN) | 需要 root | 真实隧道实验 |

### Real Linux TUN (experimental)

项目支持使用真实 Linux TUN 设备（替代 MockTunDevice）。当前状态：

- ✅ LinuxTunDevice 可成功创建真实 TUN 设备（tun0, tun1）
- ✅ Server/Client 可分别使用不同 TUN 设备启动
- ✅ Graceful shutdown 工作正常
- ⏳ 完整网络连通性需要手动配置 IP 地址和路由

**快速开始：**

```bash
# 终端 1 - 启动服务端
sudo python -m src.server --config config/server.yaml --transport tcp

# 终端 2 - 启动客户端
sudo python -m src.client --config config/client.yaml --transport tcp
```

**完整验证流程**：
- [docs/real_tun_linux.md](docs/real_tun_linux.md) - 通用 real TUN 设置和故障排查
- [docs/phase3_netns_validation.md](docs/phase3_netns_validation.md) - **推荐** 使用 network namespace 做单机可复现验证

> **Phase 3 推荐使用 network namespace**：同一 namespace 下直接使用 tun0/tun1 ping 不可靠（可能直接被 kernel 路由），建议使用 `ip netns exec` 在隔离的 namespace 中运行 server/client 进行验证。详见 [docs/phase3_netns_validation.md](docs/phase3_netns_validation.md)。

### 注意事项

1. **最小权限原则**：仅在实验环境中使用 root 权限运行 VPN 程序
2. **网络隔离**：实验环境应与生产网络隔离
3. **日志监控**：关注异常的网络行为

## 11. 路由和 NAT 实验说明

### 路由配置

项目提供路由规则生成工具（`src/forwarding/route.py`），但不自动执行：

```python
from src.forwarding.route import RouteManager

route_mgr = RouteManager()
rules = route_mgr.generate_rules(tun_ip="10.8.0.1", peer_ip="10.8.0.2")

# 查看规则（不执行）
print("\n".join(rules))

# 需手动执行（需要 root）
# route_mgr.apply_rules(rules)
```

### NAT 配置

NAT 功能用于地址转换（`src/forwarding/nat.py`）：

```bash
# 查看 NAT 规则（不执行）
python3 -c "
from src.forwarding.nat import NATManager
nat = NATManager()
print(nat.generate_rules('tun0', '10.8.0.0/24'))
"

# 手动应用（需要 root）
# iptables -t nat -A POSTROUTING -s 10.8.0.0/24 -o eth0 -j MASQUERADE
```

### 实验建议

1. 使用虚拟网络命名空间隔离实验环境
2. 使用网桥连接虚拟机进行实验
3. 记录所有网络配置便于复现

## 12. 安全边界说明

> **⚠️ 重要提醒**

本项目是**教学和研究原型**，设计用于以下场景：

- ✅ 网络协议学习与实验
- ✅ VPN 架构研究
- ✅ 安全教育与渗透测试教学（需授权）
- ✅ 课程设计与毕业设计

以下使用场景**明确禁止**：

- ❌ 未授权的网络访问
- ❌ 规避网络审计或过滤
- ❌ 隐藏网络流量以逃避监测
- ❌ 任何违法违纪的活动

### 安全建议

1. **仅在授权环境使用**：确保实验获得适当授权
2. **隔离实验环境**：使用虚拟机或容器隔离
3. **不用于生产环境**：本项目未经安全审计
4. **关注数据传输安全**：敏感数据应使用 TLS 等加密传输

### 法律提示

使用 VPN 技术时，请遵守当地法律法规。在中国境内，使用 VPN 必须通过持牌电信运营商。未经授权的 VPN 服务属于违法行为。

---

## 参考资料

- [OpenVPN 协议分析](https://openvpn.net/)
- [WireGuard 协议](https://www.wireguard.com/)
- [Python asyncio 文档](https://docs.python.org/3/library/asyncio.html)
- [Paramiko SSH 库](https://paramiko.readthedocs.io/)
- [websockets 库](https://websockets.readthedocs.io/)