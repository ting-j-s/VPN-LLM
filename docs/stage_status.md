# VPN-LLM 阶段成果状态报告

## 1. 当前阶段名称

可替换传输层 VPN 隧道基础框架稳定版

## 2. 当前代码状态

- 分支：test-2
- 稳定提交：fda32246307fe2deb4f9a5292b405010d2c7d03e
- 测试基线：143 passed, 6 skipped, 0 failed

## 3. 当前实验总目标

本项目目标是构建一个基于 LLM 辅助开发的、可替换外层传输协议的 VPN 隧道原型系统。

核心结构为：

客户端 TUN
→ ClientCore
→ Frame Codec
→ Transport Layer
→ ServerCore
→ 服务端 TUN / 路由 / NAT

其中 Transport Layer 应支持 TCP、TLS、WebSocket、SSH 等外层协议，并通过统一接口与 VPN 核心逻辑解耦。

LLM 模块不参与实时转发，只用于代码生成、重构、测试生成、错误修复和开发流程辅助。

## 4. 当前已完成模块

| 模块 | 当前状态 |
|---|---|
| Frame Codec | 已实现统一 Frame 编解码 |
| ClientCore | 已实现基础转发循环，已增加 session_id 校验 |
| ServerCore | 已实现基础转发循环，已增加 session_id 校验 |
| TCPTransport | 已实现，通过测试 |
| TLSTransport | 已实现，timeout 语义已与 TCP 统一 |
| WebSocketTransport | 已实现 dedicated background asyncio event loop，通过 localhost client/server 基础通信测试 |
| SSHTransport | 已有客户端侧实现，服务端侧仍需后续完善 |
| MockTun | 已用于单元测试 |
| LinuxTun | 已有基础实现，仍需真实 Linux 环境测试 |
| LLM 辅助模块 | 已有代码任务生成/管理雏形 |

## 5. 已完成的关键修复

1. 修复 test_requirement_trimmed 矛盾断言。
2. ClientCore / ServerCore 增加 session_id 校验，错误 session 的 Frame 会被丢弃。
3. TLSTransport timeout 语义统一为 TransportTimeout，不再把 timeout 当作连接关闭。
4. WebSocketTransport 改为每个实例使用 dedicated background asyncio event loop。
5. WebSocketTransport 的同步 connect/send/recv 接口通过 asyncio.run_coroutine_threadsafe 调用协程。
6. WebSocketTransport 增加 9 项 localhost client/server 基础通信测试。
7. README 已更新 WebSocketTransport 状态，移除 experimental 描述。
8. 已清理 __pycache__ / .pyc / .pytest_cache 跟踪问题，并完善 .gitignore。

## 6. 当前已经证明的内容

- 模块化 VPN 隧道基础架构可运行。
- VPN 核心逻辑与外层传输协议可以通过 Transport 抽象解耦。
- TCP / TLS / WebSocket 可以接入统一 Transport 接口。
- Frame 层可以统一承载不同外层协议之上的隧道数据。
- session_id 校验可以阻止错误会话 Frame 被处理。
- timeout 语义已在 TCP / TLS / WebSocket 之间趋于一致。
- LLM 辅助开发流程已经形成：人工提出需求，LLM 生成修改提示词，Vibe Coding 修改代码，测试反馈后继续迭代。

## 7. 当前尚未完成的内容

1. 真实 Linux TUN 环境下的端到端通信尚未完成。
2. 服务端 NAT / 路由转发尚未完成完整实机验证。
3. SSHTransport 服务端侧仍需进一步完善。
4. ClientCore + ServerCore + Transport 的端到端集成测试仍需补充。
5. Evaluation Layer 尚未完成，吞吐量、延迟、稳定性等指标还未形成完整实验报告。
6. LLM 模块当前仍是开发辅助，不参与运行时 VPN 转发。

## 8. 下一阶段计划

1. 补充端到端 Mock 集成测试：
   - ClientCore + TCPTransport + ServerCore + MockTun
   - ClientCore + TLSTransport + ServerCore + MockTun
   - ClientCore + WebSocketTransport + ServerCore + MockTun

2. 编写 Linux TUN 实机实验脚本：
   - scripts/setup_tun_client.sh
   - scripts/setup_tun_server.sh
   - scripts/cleanup_tun.sh
   - docs/linux_tun_experiment.md

3. 建立 Evaluation Layer：
   - scripts/benchmark_transport.py
   - docs/evaluation_plan.md
   - 记录吞吐量、延迟、连接稳定性、不同 Transport 对比结果

4. 完善 SSHTransport 服务端侧。
