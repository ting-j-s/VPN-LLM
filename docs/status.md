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

### TUN 设备
- [ ] 真实 Linux TUN 设备接入验证
- [ ] LinuxTunDevice 完整功能测试

### 网络配置
- [ ] 路由表配置
- [ ] IP 转发启用
- [ ] NAT 配置
- [ ] MTU 处理
- [ ] DNS 处理

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

Phase 2: TCP + real Linux TUN (当前)
    └── 目标：替换 MockTunDevice → LinuxTunDevice
    └── 验证：TUN 设备读写真实 IP 包

Phase 3: TCP + real TUN + 路由
    └── 目标：配置路由表、IP 转发
    └── 验证：两端可以 ping 通

Phase 4: TLS/WebSocket/SSH transport
    └── 目标：加密传输
    └── 验证：真实 TLS 证书、SSH 认证

Phase 5: LLM 协议框架
    └── 目标：动态协议替换
```

---

## 当前阶段目标 (Phase 2)

**目标**: 在 Linux 上用真实 TUN 设备替换 mock-tun

**验收标准**:
1. `python -m src.server --config config/server.yaml --transport tcp` 可用（无 --mock-tun）
2. TUN 设备可读写真实 IP 包
3. 无 root 错误或其他 TUN 相关错误
4. Graceful shutdown 仍然工作

**当前环境限制**:
- 当前用户 `xjr` 不是 root
- 没有 CAP_NET_ADMIN 权限
- 需要 sudo 或 root 才能创建 /dev/net/tun

**测试方法**（需要 root 权限）:
```bash
# 在有 root 权限的环境执行：
sudo python -m src.server --config config/server.yaml --transport tcp
```

**需要完成**:
1. LinuxTunDevice 可能需要调整（non-blocking 模式）
2. 需要 TUN 设备创建/清理脚本
3. 需要处理 `ip addr` 和 `ip route` 命令
4. 需要考虑权限降级方案

---

**最后更新**: 2026-05-02
**当前版本**: v0.1-tcp-mocktun
