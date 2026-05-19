# Phase 3.5: 真实 trace 采集与批量汇总分析

## 1. 本阶段目的

在 Phase 1/2/3 已完成工具基础上，对 VPN-LLM 自身 tcp/tls/websocket/ssh transport
采集真实 loopback 流量，生成 fingerprint report，并创建汇总脚本进行批量分析。

目标：
- 采集真实 transport 层流量（TCP handshake / TLS handshake / WebSocket upgrade）
- 生成 fingerprint report JSON
- 批量汇总为 summary.json / summary.csv
- 为后续阈值标定和 Phase 5 traffic shaper 对比提供基线

## 2. 实验矩阵

| Transport | idle | ping | curl | bulk |
|---|---|---|---|---|
| tcp | real (5 pkts) | skipped | skipped | skipped |
| tls | real (12 pkts) | skipped | skipped | skipped |
| websocket | real (9 pkts) | skipped | skipped | skipped |
| ssh | skipped | skipped | skipped | skipped |

**说明：**
- `real`: 真实 VPN-LLM server + client 启动后 tcpdump 抓取的 loopback 流量
- `skipped (mock-tun)`: ping/curl/bulk 需要真实 TUN 设备和应用层流量，`--mock-tun` 模式下仅有 heartbeat，无法区分场景
- `skipped (ssh)`: VPN-LLM SSH transport 服务端集成不完整，无法端到端启动

## 3. trace_type 定义

| 类型 | 含义 |
|---|---|
| `real` | 通过 tcpdump 在 loopback 接口采集的真实 VPN-LLM transport 流量，对应 .csv 文件存在 |
| `synthetic` | 通过 Python 脚本生成的模拟流量 CSV，用于测试或补充缺失场景 |
| `skipped` | 因环境限制（无 root / 无 TUN / 服务端未集成）无法采集 |

## 4. 如何运行 dry-run

```bash
python3 scripts/run_trace_scenarios.py run \
  --transports tcp,tls,websocket,ssh \
  --scenarios idle,ping,curl,bulk \
  --output-dir traces \
  --server-host 127.0.0.1 \
  --base-port 9000 \
  --dry-run
```

## 5. 如何运行真实采集

### 前置条件
- tcpdump / tshark 已安装
- sudo 权限（tcpdump 需要 CAP_NET_RAW）
- VPN-LLM server/client 可启动

### 单 transport 采集示例

```bash
# 1. 启动 server
python3 -m src.server --config config/server.yaml --transport tcp --mock-tun &

# 2. 抓包
sudo timeout 10 tcpdump -i lo -w traces/tcp/idle.pcap host 127.0.0.1 and port 2222 &

# 3. 启动 client
python3 -m src.client --config config/client.yaml --transport tcp --mock-tun &

# 4. 等待抓包结束，停止 server/client

# 5. pcap 转 CSV
python3 scripts/trace_capture.py convert \
  --input-pcap traces/tcp/idle.pcap \
  --output-csv traces/tcp/idle.csv \
  --client-host 127.0.0.1 \
  --server-host 127.0.0.1 \
  --server-port 2222

# 6. 生成 report
python3 -m src.evaluation.fingerprint.report \
  --input traces/tcp/idle.csv \
  --output traces/tcp/idle.report.json
```

### 差异化场景流量（需要 root + 真实 TUN）

ping/curl/bulk 场景需要真实 TUN 设备和 netns 隔离才能产生差异化流量：

```bash
# 参考 Phase 10 netns 验证脚本
sudo bash scripts/phase10_netns_tun_validation.sh --transport tcp --e2e-ping --verbose --tcpdump
```

## 6. 如何生成 summary

```bash
python3 scripts/summarize_fingerprint_reports.py \
  --input-dir traces \
  --output-json traces/summary.json \
  --output-csv traces/summary.csv
```

## 7. 如何解读 summary.csv

| 列 | 含义 |
|---|---|
| `transport` | 传输协议：tcp / tls / websocket / ssh |
| `scenario` | 场景：idle / ping / curl / bulk |
| `trace_type` | real / synthetic / skipped |
| `packet_count` | 采集到的数据包数量 |
| `risk_level` | 指纹风险等级：low / medium / high / insufficient_data |
| `fingerprint_risk_score` | 综合风险评分 (0.0–1.0) |
| `small_packet_ratio` | 小包比例（<128B），高值提示固定长度小包 |
| `repeated_length_ratio` | 重复长度比例，高值提示包长熵低 |
| `ngram_entropy` | 3-gram 序列熵，低值提示模式固定 |
| `dominant_ngram_ratio` | 主导 3-gram 占比 |
| `burst_count` | 突发数量 |
| `max_burst_size` | 最大突发大小（字节） |
| `dominant_burst_direction_ratio` | 主导突发方向比例（0.5=均衡，1.0=单向） |
| `avg_inter_arrival_ms` | 平均包间到达间隔（毫秒） |
| `notes` | 指纹风险因子说明 |

### 关键解读规则

- **risk_level = low**: 流量模式接近随机，不易指纹识别
- **risk_level = medium**: 存在一定可识别模式
- **risk_level = high**: 流量具有强指纹特征，容易被 DPI 识别
- **small_packet_ratio + repeated_length_ratio 同时高**: 大量固定长度小包，典型指纹特征
- **ngram_entropy 低 + dominant_ngram_ratio 高**: 包序列模式高度重复

## 8. 当前第一批结果

### 采集环境
- 平台: Debian 12, Linux 6.1
- Python: 3.11.2
- tcpdump/tshark: 已安装
- 模式: --mock-tun (无需 root 创建 TUN 设备)
- 接口: lo (loopback)

### 结果摘要

| Transport | Scenario | trace_type | packets | risk_level | risk_score | ngram_entropy |
|---|---|---|---|---|---|---|
| tcp | idle | real | 5 | insufficient_data | 0.0 | 0.0 |
| tls | idle | real | 12 | medium | ~0.45 | ~0.7 |
| websocket | idle | real | 9 | insufficient_data | 0.0 | 0.0 |
| tcp/tls/websocket | ping/curl/bulk | skipped | - | - | - | - |
| ssh | all | skipped | - | - | - | - |

### 关键发现
1. **TCP idle**: 5 个包，低于 risk scoring 最低阈值 (5)，标记为 insufficient_data
2. **TLS idle**: 12 个包，包含 TLS 握手，呈现 medium 风险（TLS 握手模式可识别）
3. **WebSocket idle**: 9 个包，包含 HTTP upgrade + WebSocket 帧，但因包数少而不足判断
4. **所有 mock-tun 场景流量完全相同**: 只有 heartbeat，无应用层数据

## 9. 限制

- **git fetch 失败**: 代理 127.0.0.1:17890 不可达，不影响本地采集
- **SSH transport**: VPN-LLM SSH 服务端集成不完整，无法端到端启动
- **TUN/netns 需要 root**: `--mock-tun` 模式下无真实 IP 包穿越隧道，所有场景流量相同
- **loopback vs 真实网络**: loopback 延迟为零，无丢包，无乱序，与真实网络行为不同
- **sudo 密码依赖**: tcpdump 抓包需要 sudo，自动化受限
- **包数不足**: 短时捕获 (<10s) 导致包数少，部分报告为 insufficient_data
- **端口冲突**: 默认端口 2222 可能被占用，需使用备用端口

## 10. 下一步

1. **Phase 4 CalcuLatency RTT 测量**: 在 trace 采集同时记录应用层 RTT，与包间到达间隔交叉验证
2. **阈值标定**: 用更多样本标定 Phase 1/2 的 risk 因子阈值，替代当前经验值
3. **Phase 5 traffic shaper**: 实现后复跑相同矩阵做 before/after 对比
4. **真实 TUN + netns**: 在有 root 环境下采集 ping/curl/bulk 差异化流量
5. **长时间采集**: 增加采集时长 (>60s) 获得更多包数，提高统计显著性
6. **SSH transport 完善**: 完成服务端 SSH transport 集成后补充采集
