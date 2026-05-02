# Project Status

## 已完成

### 核心架构
- [x] 基础包结构 (`src/`, `tests/`, `config/`)
- [x] Python 模块导入（相对导入修复）
- [x] CLI 参数 (`server.py`, `client.py`)
- [x] 配置加载 (`common/config.py`)

### 数据层
- [x] Frame 编解码 (`common/frame.py`)
- [x] Frame 边界检查（oversized payload, trailing data）
- [x] Length prefix framing (4-byte big-endian)

### 传输层
- [x] Transport 基类抽象
- [x] TCPTransport connect / accept
- [x] MockTransport（内存队列，用于单元测试）

### TUN 设备
- [x] TunDevice 抽象基类
- [x] MockTunDevice（无 root 依赖）
- [x] LinuxTunDevice（需要 root）
- [x] LinuxTunDevice.open() 修复（struct.pack 替代 array 索引）

### 核心逻辑
- [x] ClientCore 启动顺序（transport.connect → is_connected → threads）
- [x] ServerCore 启动顺序（transport.connect → accept → is_connected → threads）
- [x] `tun_to_transport_loop`
- [x] `transport_to_tun_loop`
- [x] `heartbeat_loop`

### 安全
- [x] TLS: `insecure_skip_verify` 默认 False
- [x] TLS: `verify_server=True` 时加载系统默认 CA
- [x] TLS: `insecure_skip_verify=True` 时打印 WARNING
- [x] SSH: `auto_add_host_key` 默认 False（使用 RejectPolicy）

### 优雅关闭
- [x] Graceful shutdown 流程
- [x] `stop()` idempotent（可重复调用）
- [x] shutdown 日志级别（ERROR → WARNING/DEBUG）
- [x] stop() 顺序优化（close transport/tun → join threads）

### 测试
- [x] 112 tests passing
- [x] 单向 Core 数据路径测试通过（Client→Server, Server→Client）
- [x] MockTransport loopback 测试通过
- [x] TLS/TCP/WebSocket/Mock transport 测试通过
- [x] Frame decode 边界测试（oversized, trailing data）
- [x] LinuxTunDevice 单元测试（root 权限下跳过）

## 部分完成

### TLSTransport
- [x] 安全默认值已修复
- [ ] 集成测试不足（需要真实证书）
- [ ] 自签名证书场景测试

### SSHTransport
- [x] 安全默认值已修复（RejectPolicy）
- [ ] 集成测试不足（需要真实 SSH 服务器）
- [ ] Host key 验证流程测试

### WebSocketTransport
- [x] 基础实现完成
- [x] README 标注为 experimental
- [ ] 多线程环境稳定性未验证
- [ ] asyncio 与线程混合问题待解决

### 双向 Core 数据路径
- [x] 测试代码存在
- [ ] `test_bidirectional_data_path` 因 timing 敏感性跳过
- [ ] 单向测试已覆盖主要场景

## 未完成

### 跨主机测试
- [ ] 同一机器 client/server 通信
- [ ] 两台机器 client/server 通信
- [ ] 跨网络场景

### 传输层
- [ ] TLS 端到端测试（真实证书）
- [ ] SSH transport 集成测试
- [ ] WebSocket 稳定性改进

### LLM 辅助
- [ ] 代码生成辅助框架
- [ ] 协议替换框架

---

## 技术路线图

```
Phase 1: TCP + mock-tun (v0.1-tcp-mocktun) ✅ 已完成
    └── 验证：TCP 连接、MockTUN、Graceful Shutdown

Phase 2: TCP + real Linux TUN ✅ 已完成
    ├── Phase 2-A: LinuxTunDevice 创建/读写能力
    │   └── 修复：struct.pack("16sH") 替代 array 索引
    ├── Phase 2-B: Server/Client real TUN 启动
    │   └── 验证：tun0/tun1 可同时启动，Graceful shutdown 工作
    └── 状态：v0.1-tcp-realtun (2026-05-02)

Phase 3: TCP + real TUN + 路由 + 连通性验证 ⏳ 进行中
    └── 目标：ip addr / ip route / ping / tcpdump 验证真实 IP 包流动

Phase 4: TLS/WebSocket/SSH transport
    └── 目标：加密传输
    └── 验证：真实 TLS 证书、SSH 认证

Phase 5: LLM 协议框架
    └── 目标：动态协议替换
```

---

## Phase 2 完成总结 (2026-05-02)

### 已完成

**Phase 2-A: LinuxTunDevice 创建/读写能力**
- [x] LinuxTunDevice.open() 修复 - 使用 `struct.pack("16sH", name_bytes, flags)` 替代 Python array 索引
- [x] ifreq 结构正确构造，ioctl 调用成功
- [x] TUN 设备可成功创建 (tun0, tun1)

**Phase 2-B: Server/Client real TUN 启动**
- [x] Server 使用 tun0，Client 使用 tun1（配置文件指定不同设备名）
- [x] Server/Client 可同时运行
- [x] Graceful shutdown 工作正常（Ctrl+C 可干净退出）
- [x] 无 root 错误或其他 TUN 相关错误

### 验证方法

```bash
# 终端 1 - 服务端
sudo python -m src.server --config config/server.yaml --transport tcp

# 终端 2 - 客户端
sudo python -m src.client --config config/client.yaml --transport tcp
```

预期输出：
- Server: "LinuxTunDevice 'tun0' opened (FD=3, MTU=1400)"
- Client: "LinuxTunDevice 'tun1' opened (FD=3, MTU=1400)"
- Server: "TCP connection accepted from ('127.0.0.1', ...)"
- Client: "TCP client connected"

---

## Phase 3: real TUN + 路由 + ping 连通性

### 目标

让项目具备清晰的真实 TUN 验证流程，证明 tun0/tun1 不只是能创建，还能通过 IP 地址、路由和 ping/tcpdump 验证真实 IP 包流动。

### 验收标准

1. 手动配置 IP 地址后，tun0/tun1 可显示正确 IP
2. `ip route` 可看到 tunnel 路由
3. `ping -I tun1 10.8.0.1` 和 `ping -I tun0 10.8.0.2` 可连通
4. `tcpdump -i tun0 -n icmp` 可看到 ICMP 包流动

### 文档

详见 [docs/real_tun_linux.md](real_tun_linux.md)

### 手动验证步骤

```bash
# 1. 配置 IP 地址
sudo ip addr add 10.8.0.1/24 dev tun0
sudo ip link set tun0 up
sudo ip addr add 10.8.0.2/24 dev tun1
sudo ip link set tun1 up

# 2. 启用 IP 转发（Server）
sudo sysctl -w net.ipv4.ip_forward=1

# 3. 添加路由
sudo ip route add 10.8.0.0/24 dev tun0  # Server
sudo ip route add 10.8.0.0/24 dev tun1  # Client

# 4. ping 验证
ping -I tun1 10.8.0.1  # 从 Client ping Server
ping -I tun0 10.8.0.2  # 从 Server ping Client

# 5. tcpdump 抓包
sudo tcpdump -i tun0 -n icmp
```

### 注意事项

- 本阶段**不自动修改系统路由**，只提供命令和文档
- 完整网络连通性仍依赖手动 ip addr / ip route 配置
- 需要 root 或 CAP_NET_ADMIN 权限

---

**最后更新**: 2026-05-02
**当前版本**: v0.1-tcp-realtun
**下一阶段**: Phase 3 - real TUN + 路由 + ping 连通性验证