# 本地离线 trace 采集与场景运行器 (Local Offline Trace Capture & Scenario Runner)

## 1. Phase 3 目的

为 Phase 1/2 的 fingerprint evaluator 提供**自动化、可复现**的本地 CSV trace
生成能力。通过对 VPN-LLM 自身的 tcp/tls/websocket/ssh transport 在各种场景
(idle/ping/curl/bulk/reconnect) 下采集流量，生成 Phase 1/2 所需的 CSV 文件，
并批量运行 fingerprint report。

所有操作均为本地、离线、受控实验——不扫描第三方，不阻断流量，不修改 VPN core。

## 2. 与 Phase 1/2 的关系

```
Phase 1: FlowFeatures + risk scoring (pcap_features.py)
Phase 2: 3-gram + burst features (ngram_features.py, burst_features.py)
Phase 3: trace capture + scenario runner (trace_capture.py, run_trace_scenarios.py)
         ↓
         生成 CSV → Phase 1/2 evaluate → JSON report
```

Phase 3 不改变前两阶段的任何逻辑，仅作为 **数据采集层** 提供输入。

## 3. 工具依赖

| 工具 | 必需? | 用途 |
|---|---|---|
| `tcpdump` | 可选 | 抓包 |
| `tshark` | 可选 | pcap 转 CSV |
| `timeout` | 可选 | 限制抓包时长 |
| `ip` | 可选 | 网络接口检测 |

默认所有命令支持 `--dry-run`，在无 root / 无 tcpdump 环境下不会直接失败。

## 4. CSV 格式

与 Phase 1 完全一致：

```csv
timestamp,src,dst,src_port,dst_port,proto,length,direction
0.000,10.0.0.1,10.0.0.2,45001,9000,tcp,64,C2S
0.005,10.0.0.2,10.0.0.1,9000,45001,tcp,64,S2C
```

## 5. trace_capture.py 用法

### 5.1 工具检测

```bash
python3 scripts/trace_capture.py tools
# {"tcpdump": true, "tshark": true, "timeout": true, "ip": true}
```

### 5.2 dry-run 抓包

```bash
python3 scripts/trace_capture.py capture \
  --interface lo \
  --host 127.0.0.1 \
  --port 9000 \
  --duration 10 \
  --output-pcap traces/tcp_idle.pcap \
  --dry-run
```

### 5.3 真正抓包（需要 root / CAP_NET_RAW）

```bash
sudo python3 scripts/trace_capture.py capture \
  --interface lo \
  --host 127.0.0.1 \
  --port 9000 \
  --duration 10 \
  --output-pcap traces/tcp_idle.pcap
```

### 5.4 pcap 转 CSV

```bash
python3 scripts/trace_capture.py convert \
  --input-pcap traces/tcp_idle.pcap \
  --output-csv traces/tcp_idle.csv \
  --client-host 127.0.0.1 \
  --server-host 127.0.0.1 \
  --server-port 9000
```

### 5.5 方向推断规则

- `src == client_host && dst == server_host` → C2S
- `src == server_host && dst == client_host` → S2C
- 同 host 时通过 `server_port` 或 `client_port` 辅助判断
- 无法判断时抛出 `ValueError`

## 6. run_trace_scenarios.py 用法

### 6.1 生成计划 (plan)

```bash
python3 scripts/run_trace_scenarios.py plan \
  --transports tcp,tls,websocket,ssh \
  --scenarios idle,ping,curl,bulk \
  --output-dir traces \
  --server-host 127.0.0.1 \
  --base-port 9000
```

输出 manifest JSON：

```json
{
  "server_host": "127.0.0.1",
  "base_port": 9000,
  "output_dir": "traces",
  "entry_count": 16,
  "entries": [
    {
      "scenario": "idle",
      "transport": "tcp",
      "server_port": 9000,
      "pcap_path": "traces/tcp/idle.pcap",
      "csv_path": "traces/tcp/idle.csv",
      "report_path": "traces/tcp/idle.report.json",
      "scenario_command": "# no traffic — just keep the tunnel open",
      "capture_command": "timeout 10 tcpdump -i lo -w traces/tcp/idle.pcap host 127.0.0.1 and port 9000",
      "convert_command": "tshark -r traces/tcp/idle.pcap -T fields ...",
      "report_command": "python3 -m src.evaluation.fingerprint.report --input traces/tcp/idle.csv --output traces/tcp/idle.report.json"
    }
  ]
}
```

### 6.2 dry-run 运行

```bash
python3 scripts/run_trace_scenarios.py run \
  --transports tcp \
  --scenarios idle \
  --output-dir traces \
  --server-host 127.0.0.1 \
  --base-port 9000 \
  --dry-run
```

### 6.3 真正执行

```bash
python3 scripts/run_trace_scenarios.py run \
  --transports tcp,tls \
  --scenarios idle,ping \
  --output-dir traces \
  --server-host 127.0.0.1 \
  --base-port 9000 \
  --execute
```

执行流程：
1. 确保输出目录存在
2. 启动 tcpdump 抓包（timeout 控制时长）
3. 运行场景命令（如 ping / curl）
4. 等待抓包结束
5. tshark 转换 pcap → CSV
6. 运行 fingerprint report 生成 JSON

## 7. 推荐实验矩阵

| Transport | 端口 | idle | ping | curl | bulk | reconnect |
|---|---|---|---|---|---|---|
| tcp | 9000 | ✓ | ✓ | ✓ | ✓ | ✓ |
| tls | 9001 | ✓ | ✓ | ✓ | ✓ | ✓ |
| websocket | 9002 | ✓ | ✓ | ✓ | ✓ | ✓ |
| ssh | 9003 | ✓ | ✓ | ✓ | ✓ | ✓ |

不包含 http2（当前 VPN-LLM baseline 无此 transport）。

## 8. 输出目录结构

```
traces/
  tcp/
    idle.pcap
    idle.csv
    idle.report.json
    ping.pcap
    ping.csv
    ping.report.json
    curl.pcap
    curl.csv
    curl.report.json
    ...
  tls/
    ...
  websocket/
    ...
  ssh/
    ...
```

## 9. 注意事项

### 9.1 安全边界
- 只采集本地受控实验流量
- 不采集第三方流量
- 不实现真实 DPI 系统
- 所有分析基于本地 CSV trace 文件

### 9.2 权限要求
- tcpdump 需要 `root` 或 `CAP_NET_RAW` capability
- 如果无权限，使用 `--dry-run` 生成命令后手动执行
- lo 接口抓包通常不需要特殊权限（取决于系统配置）

### 9.3 git fetch 失败
- 由于代理 127.0.0.1:17890 不可达，`git fetch origin` 会失败
- 不影响本地实验，仅意味着远端引用可能过期
- 基于本地缓存进行所有操作

### 9.4 环境依赖
- tcpdump 和 tshark 可选，缺失时脚本会提示并建议使用 dry-run
- 所有测试不依赖真实的 tcpdump/tshark

## 10. 后续与 CalcuLatency / cross-layer RTT 的关系

- **CalcuLatency 集成**：Phase 3 采集的 trace 已包含毫秒级 timestamp，
  可直接计算 RTT 相关特征，与 CalcuLatency 的主动 RTT 测量互补
- **cross-layer 分析**：同一场景的 pcap trace + 应用层 log + 内核态
  统计可以联合分析，暴露各层的指纹差异
- **traffic shaper 验证**：Phase 5 traffic shaper 实现后，可用本模块
  批量采集 shaping 前后的 trace 并对比 risk_score，量化去指纹效果
- **阈值标定**：用采集到的真实 VPN-LLM trace 标定 Phase 1/2 的 risk
  因子阈值，替代当前经验值

## 11. 当前限制

- 依赖外部工具 tcpdump/tshark，非纯 Python 方案
- 场景命令为模板，需根据实际 VPN-LLM 部署调整
- 未实现跨多次采集的 first_n_lengths 稳定性对比
- 未集成 CalcuLatency 主动 RTT 测量
- 桶边界和 risk 阈值仍为经验值
