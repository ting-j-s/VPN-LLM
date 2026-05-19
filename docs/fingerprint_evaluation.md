# 本地离线可指纹性评估器 (Local Offline Fingerprintability Evaluator)

## 1. 模块目的

本模块为 VPN-LLM 项目提供**本地、离线、受控**的流量可指纹性评估能力。它不实现真实
DPI 系统，不扫描第三方，不阻断流量，不修改 VPN core / transport / frame codec 的
运行时逻辑。它仅作为 evaluation 层的纯观测工具，帮助开发者理解不同 Transport × Core
组合的流量特征暴露程度。

## 2. 背景：为什么 VPN 流量可被指纹识别

1OpenVPN.pdf 的核心观点：VPN 指纹不只来自明文字段（如 TLS SNI、证书、协议头），
还可以来自：

- **包长分布 (packet length distribution)** — 握手阶段固定长度的 ACK/控制报文
  形成可重复的长度序列；
- **方向序列 (direction sequence)** — 客户端和服务端的对话模式在早期握手阶段
  高度稳定；
- **服务端行为 (server-side behaviour)** — 服务端对特定输入的响应模式在不同
  实现中具有区分度；
- **时间特征 (timing)** — 包间间隔的均值和方差在特定实现中可能具有特征性。

本模块将这些思路转化为可解释的启发式特征，不依赖机器学习，所有评分规则均可审计。

## 3. 设计原则

- **仅本地离线使用** — 输入为事先采集的 CSV trace 文件，不实时抓包；
- **不检测第三方** — 不实现针对 OpenVPN、WireGuard 或任何第三方 VPN 的检测器；
- **标准库优先** — 无重型依赖（numpy、scapy、dpkt 等均不引入）；
- **可解释评分** — 风险分数由加权规则合成，每个因子的贡献可追溯。

## 4. CSV 输入格式

```csv
timestamp,src,dst,src_port,dst_port,proto,length,direction
0.000,10.0.0.1,10.0.0.2,45001,8080,tcp,64,C2S
0.005,10.0.0.2,10.0.0.1,8080,45001,tcp,64,S2C
0.012,10.0.0.1,10.0.0.2,45001,8080,tcp,128,C2S
0.018,10.0.0.2,10.0.0.1,8080,45001,tcp,128,S2C
0.025,10.0.0.1,10.0.0.2,45001,8080,tcp,1400,C2S
0.030,10.0.0.2,10.0.0.1,8080,45001,tcp,1400,S2C
```

字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `timestamp` | float | 秒级时间戳（或可被 float() 解析的字符串） |
| `src` | str | 源地址 |
| `dst` | str | 目的地址 |
| `src_port` | int | 源端口 |
| `dst_port` | int | 目的端口 |
| `proto` | str | 协议标签：tcp / udp / ws / tls / ssh 等 |
| `length` | int | 可观测包长度（字节） |
| `direction` | str | 仅允许 `C2S`（客户端→服务端）或 `S2C`（服务端→客户端） |

## 5. 运行方式

```bash
# 输出到 stdout
python3 -m src.evaluation.fingerprint.report --input traces/sample.csv

# 输出到 JSON 文件
python3 -m src.evaluation.fingerprint.report --input traces/sample.csv --output report.json

# 调整参数
python3 -m src.evaluation.fingerprint.report \
    --input traces/sample.csv \
    --first-n 20 \
    --small-packet-threshold 100 \
    --ngram-n 3 \
    --burst-gap-ms 10 \
    --top-k 5 \
    --output report.json
```

## 6. 指标解释

### first_n_lengths / first_n_directions

前 N 个包（默认 N=30）的长度和方向序列。用于检查握手早期的稳定性。
如果多轮握手中 first_n_lengths 完全相同，说明握手阶段有高度可指纹的模式。

### small_packet_ratio

长度小于阈值（默认 128 字节）的包的比例。ACK、控制报文、心跳等通常是小包。
高小包比例意味着流量中有大量控制/信令包，可能暴露协议状态机。

### repeated_length_ratio

出现次数 >1 的包长度占总包数的比例。高重复比意味着包长集中在少数几个值上，
形成可辨识的"长度分布指纹"。

### unique_length_count

不同包长度的数量。低值（如全部包只有 2-3 种长度）是强指纹信号。

### inter_arrival（avg / median / stdev，单位 ms）

包间间隔的均值、中位数和标准差。标准差相对于均值的变异系数 (CV) 越低，
说明包到达时间越规律，越容易被建模和匹配。

### max_burst_packets_10ms

任意 10 ms 时间窗口内的最大包数。高突发度（如 >5）可能暴露特定的
批量发送策略，也是可指纹特征。

### direction_switch_count

前 N 个包中方向的切换次数。切换模式过于固定（如完美交替 C2S-S2C-C2S-S2C
或完全单向）比随机混合更容易被指纹识别。

### fingerprint_risk_score

加权合成的可指纹性风险分数（0.0 ~ 1.0），由以下因子加权求和：

| 因子 | 权重 | 说明 |
|---|---|---|
| repeated_length_ratio | 0.20 | 包长重复度 |
| small_packet_ratio | 0.16 | 小包比例 |
| inter-arrival regularity | 0.16 | 时间规律性 |
| direction-switch fixedness | 0.16 | 方向切换模式固定性 |
| burst (packet-level) | 0.12 | 10ms 窗口突发度 |
| ngram_pattern_score | 0.10 | 3-gram 模式稳定性 |
| burst_pattern_score | 0.10 | burst 方向/大小可辨识度 |

风险等级划分：

| 分数区间 | 风险等级 |
|---|---|
| < 0.35 | `low` |
| 0.35 ~ 0.70 | `medium` |
| >= 0.70 | `high` |
| 包数 < 5 | `insufficient_data` |

## 7. 后续扩展

### 数据采集

- 使用 `tcpdump` / `tshark` 抓包后转 CSV，格式保持 `timestamp,src,dst,src_port,dst_port,proto,length,direction`；
- 对每种 Transport（tcp / tls / websocket / ssh）分别采集 idle、ping、curl、大文件传输等场景的 trace；
- 每种场景至少采集 10 次，检查 first_n_lengths 的跨次稳定性。

### 特征增强

- **3-gram / n-gram 分析** — 参考 2Fingerprinting Obfuscated Proxy Traffic with
  Encapsulated TLS Handshakes，对 (length, direction) 二元组序列提取 n-gram
  频率分布，进一步捕捉握手阶段的稳定模式；
- **burst 分布分析** — 不只取 max burst，而是统计 burst 大小分布
  （P50 / P95 / P99），暴露批量发送策略；
- **流量整形对比** — 在 traffic shaping 前后分别运行评估器，对比 risk_score
  的变化，量化 shaping 的去指纹效果；
- **主动探测抵抗** — 后续可扩展为注入畸变包（malformed input），检查
  服务端响应模式是否一致（一致性 = 可指纹）。

### 集成

- 接入 LLM Agent 的 ValidationRunner 管线，作为新的 Gate；
- Transport 替换前后对比 risk_score，作为替换验证的量化指标。

## 7.5. Encapsulated TLS Handshake 风险背景

参考 *2Fingerprinting Obfuscated Proxy Traffic with Encapsulated TLS Handshakes*，
该研究关注的不是外层传输协议字段，而是**隧道内部的 TLS 握手在 size / timing /
direction 三维度上形成的稳定模式**：

- **signed packet size sequence**：将每个包的 TCP 层可观测长度编码为有符号值，
  C2S 为正值、S2C 为负值，形成一维序列；
- **3-gram**：从 signed size sequence 中提取滑动窗口 n-gram（默认 n=3），
  统计高频模式。例如 ``+L2|-L4|-L4`` 在 TLS 握手中反复出现（ClientHello →
  ServerHello + Certificate 分片）；
- **burst**：同方向连续包按时间间隔阈值聚合为 burst，观察 burst 大小和方向
  分布。大下行 burst（服务端证书链）后紧跟小上行 burst（客户端 ACK / CCS）
  是典型的握手形状。

本模块利用这些特征评估 VPN-LLM 自身 trace 是否存在类似模式，**不针对任何
第三方代理或 VPN 产品**。

## 7.6. 3-gram 特征

### signed size 编码

- `signed_size(packet)`: C2S 返回正 length，S2C 返回负 length；
- `length_bucket(length)`: 将包长映射到 4 个桶（L1: 1-160, L2: 161-600,
  L3: 601-1210, L4: 1211+），length=0 归入 L0；
- `signed_bucket(packet)`: 组合方向与桶，如 ``+L2``、``-L4``。

### 3-gram 分析

- `extract_ngrams(buckets, n=3)`: 滑动窗口提取 n-gram；
- `ngram_entropy(counts)`: Shannon 熵，值越低说明序列重复性越高；
- `top_ngrams(counts, k=10)`: 最高频 n-gram 及出现 ratio；
- `dominant_ngram_ratio`: 最高频 n-gram 占总 n-gram 的比例。

### 指纹含义

- **dominant_ngram_ratio 高** → 局部包长/方向模式重复明显，如 TLS 握手阶段
  固定出现的 ``+L2|-L4|-L4`` 序列；
- **ngram_entropy 低** → 序列可变性低，流量行为高度可预测；
- **top_ngrams** → 用于后续报告观察是否出现类似 TLS-handshake-like 形状。

## 7.7. Burst 特征

### 定义

**Burst** = 连续同方向 packet，且相邻 packet 的 inter-arrival 时间 ≤ max_gap_ms
（默认 10ms），合并为一个 burst。方向变化或 gap > 阈值均开启新 burst。

### 统计指标

| 指标 | 说明 |
|---|---|
| `burst_count` | burst 总数 |
| `burst_total_lengths` | 各 burst 的总字节列表 |
| `burst_directions` | 各 burst 的方向列表 |
| `avg_burst_size` | 平均 burst 大小 (bytes) |
| `median_burst_size` | 中位 burst 大小 |
| `p95_burst_size` | P95 burst 大小 |
| `max_burst_size` | 最大 burst 大小 |
| `avg_burst_packet_count` | 平均每 burst 包数 |
| `max_burst_packet_count` | 最大 burst 包数 |
| `c2s_burst_count` / `s2c_burst_count` | 各方向 burst 数 |
| `direction_switch_count_between_bursts` | burst 间的方向切换次数 |
| `dominant_burst_direction_ratio` | 主导方向 burst 占比 |

### 指纹含义

- **大下行 burst**（服务端证书链、大响应）后紧跟**小上行 burst**（ACK / CCS）→
  典型 TLS 握手形状；
- **dominant_burst_direction_ratio 过高** → 流量方向严重偏斜，可能暴露客户端/
  服务端角色；
- **max_burst_size 很大且方向集中** → 可能存在固定大小的批量传输模式。

## 7.8. 风险评分更新

在 Phase 1 的 5 因子基础上，新增 2 个可解释因子：

| 因子 | 权重 | 说明 |
|---|---|---|
| ngram_pattern_score | 0.10 | dominant_ngram_ratio 高 + ngram_entropy 低 → 模式稳定 |
| burst_pattern_score | 0.10 | dominant_burst_direction_ratio 高 + max/avg burst size 比大 → burst 可辨识 |

原有 5 因子权重按比例缩放（总权重保持 1.0），所有阈值均为经验值。

## 7.9. 当前实现限制

- 仅本地特征分析，不训练分类器；
- 不声称能检测真实代理或 VPN 产品；
- 桶边界和 risk 阈值是经验性的，需后续标定；
- 未实现 1OpenVPN.pdf 中的 n-gram 包长×方向联合统计分析；
- 未实现 2Fingerprinting 中的 CDF / Wasserstein 距离等统计检验。

## 7.10. 后续实验计划

1. 多次采集同一 Transport 的 idle / ping / curl / 大文件传输 trace；
2. 对比 baseline 和 traffic shaping 后的 `ngram_entropy`、`dominant_ngram_ratio`、`burst` 分布；
3. 为 Phase 5 traffic shaper 提供量化指标；
4. 接入 CalcuLatency 做主动 RTT 测量，补充时序维度。

## 8. 安全边界

- 本模块**仅用于授权实验和教学研究**；
- 不实现真实 DPI 系统；
- 不针对任何第三方 VPN 协议；
- 所有分析基于本地 CSV trace 文件，不实时抓包或拦截流量。
