# VPN Tunnel System - 基于 LLM 辅助的可替换传输层 VPN 隧道系统

## 项目目标

本项目实现一个模块化 VPN 隧道原型，用于在授权实验环境下研究 VPN 隧道技术。

**核心功能**：
- 客户端从 TUN 虚拟网卡读取 IP 包
- 将 IP 包封装成统一 Frame 格式
- 通过可替换的 Transport 层发送到服务端
- 服务端解 Frame 后转发到目标网络

**设计原则**：
- 核心逻辑与传输协议完全解耦
- 支持 Transport 层灵活替换（SSH → TCP → TLS → WebSocket）
- 引入 LLM 辅助开发，自动生成模块代码

## 阶段性目标

### 第一阶段：基础原型（TUN + SSH + NAT）
- [x] 实现 SSH Transport
- [x] 实现 Frame 统一编解码
- [x] 实现客户端/服务端核心逻辑
- [x] 支持 Mock TUN 测试

### 第二阶段：统一框架（多协议支持）
- [ ] 实现 TCP Transport
- [ ] 实现 TLS/HTTPS Transport
- [ ] 实现 WebSocket/WSS Transport
- [ ] 配置动态切换

### 第三阶段：LLM 接入
- [ ] LLM 代码生成模块
- [ ] 配置自动生成
- [ ] 错误自动修复

## 运行方式

### 环境要求
- Python 3.10+
- SSH 服务端（用于第一阶段）

### 安装依赖
```bash
pip install -r requirements.txt
```

### 配置
编辑 `config/client.yaml` 和 `config/server.yaml`

### 运行
```bash
# 启动服务端
python -m src.server

# 启动客户端
python -m src.client
```

## 目录结构

```
vpn_tunnel/
├── README.md
├── requirements.txt
├── config/
│   ├── client.yaml      # 客户端配置
│   └── server.yaml      # 服务端配置
├── src/
│   ├── common/          # 公共模块
│   │   ├── frame.py     # 统一帧格式
│   │   ├── config.py    # 配置加载
│   │   ├── logger.py    # 日志
│   │   └── errors.py    # 异常定义
│   ├── tun/             # TUN 设备抽象
│   │   └── tun_device.py
│   ├── transport/        # 传输层抽象
│   │   ├── base.py      # Transport 基类
│   │   └── ssh_transport.py
│   ├── core/            # 核心逻辑
│   │   ├── client_core.py
│   │   └── server_core.py
│   ├── forwarding/      # 转发层
│   │   ├── nat.py
│   │   └── route.py
│   ├── evaluation/      # 评估模块
│   │   └── stats.py
│   ├── llm/             # LLM 辅助模块
│   │   ├── llm_client.py
│   │   └── code_task_manager.py
│   ├── client.py
│   └── server.py
└── tests/
    ├── test_frame.py
    ├── test_config.py
    └── test_transport_mock.py
```

## 注意事项

本项目仅用于授权实验环境下的隧道技术研究，**不**实现任何规避检测、隐藏流量或绕过审计的功能。
