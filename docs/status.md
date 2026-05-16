# Project Status

## 当前阶段: Phase 10.6 — LLM 驱动的 Transport/Core 替换闭环

**核心定位**: 本项目是 LLM 驱动的 Transport/Core 替换实验平台，不是单一 VPN 实现。

- **Transport 外层可替换**: mock / tcp / tls / websocket / ssh
- **VPN Core 内核可替换**: session / frame / forwarding / TUN loop
- **Replacement smoke matrix 是 Gate 1**
- **netns + real TUN validation 是 Gate 2**
- **LLM Agent 不参与实时转发** — 只参与开发、补丁、验证和报告闭环

## 验证 Gate 体系

| Gate | 目的 | 状态 |
|---|---|---|
| Gate 1 | pytest 单元/集成测试 (562+ passed, 6 skipped) | PASS |
| Gate 2 | Replacement smoke matrix | PASS |
| Gate 3 | netns + TUN 环境验证 | PASS |
| Gate 4 | netns + TUN E2E ping (TCP / WebSocket) | PASS |
| Gate 5 | benchmark / stability | Phase 10.7 planned |

## 已完成

### 核心架构
- [x] Transport 抽象层 (mock/tcp/tls/websocket/ssh)
- [x] Frame 编解码 (VTUN magic + session_id + 4-byte length prefix)
- [x] ClientCore / ServerCore (forwarding loop + heartbeat + session_id 校验)
- [x] MockTunDevice / LinuxTunDevice
- [x] 配置加载与验证 (config.py)

### LLM Agent 框架
- [x] TaskPlanner (rule-based + LLM-based)
- [x] LLMPatchGenerator (unified diff + secret scanning)
- [x] SafetyGuard (路径/命令安全检查)
- [x] ValidationRunner (compileall / pytest / git status / git apply)
- [x] ReplacementValidator (smoke matrix)
- [x] CommitAdvisor (Conventional Commits 建议)
- [x] TaskRecordManager (任务目录管理)
- [x] ReportWriter (结构化 Markdown 报告)
- [x] CLI 入口 (scripts/llm_task.py) 完整参数门控

### LLM Agent 关键安全边界
- [x] 永不自动 git push
- [x] 永不自动 git commit
- [x] --apply-patch 是显式人工闸门
- [x] SafetyGuard 阻断危险命令和敏感路径
- [x] Secret scanning 在 patch 生成阶段执行

### netns + TUN E2E 验证
- [x] Phase 10.3: netns + TUN 环境脚本
- [x] Phase 10.4: 共享 session_id 支持
- [x] Phase 10.5: TCP netns E2E ping 通过
- [x] Phase 10.6: WebSocket netns E2E ping 通过

### 测试
- [x] 562+ tests passing, 6 skipped, 0 failed
- [x] Transport × Core smoke matrix CI 安全
- [x] LLM task CLI 集成测试 (mocked validation, 自包含 temp git repo)

## 部分完成

### SSHTransport
- [x] 客户端侧实现
- [ ] 服务端侧集成

### Core variant 扩展
- [ ] strict_session variant
- [ ] alt_frame_codec variant
- [ ] experimental_forwarding variant

## 下一阶段

### Phase 10.7: 稳定性与性能 benchmark
- 长时间运行稳定性测试
- 吞吐量和延迟对比 (各 Transport 间)
- 资源占用评估

### Core variant 扩展
- 实现多个 Core variant 并通过替换闭环验证

---

**最后更新**: 2026-05-15
**当前分支**: test-2
**当前提交**: 76d1b47
**当前阶段**: Phase 10.6 — LLM 驱动的 Transport/Core 替换闭环
