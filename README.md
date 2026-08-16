# hermes-nodes

让 Hermes 通过一个 hub 动态注册并调用**多台设备**的工具 —— OpenClaw Nodes 的 Hermes 原生实现。

```
┌─ 每台设备 (hermes-node) ────────┐      ┌─ Hermes 进程内 (hermes-node-hub 插件) ─┐
│  本地工具:                       │      │  HTTP+WS 服务器 (默认 127.0.0.1:9721)    │
│  · exec_command  执行命令        │─────▶│  WS 注册 (hello/心跳/调用/结果)         │
│  · read_file     读文件(任意格式) │ WS   │  注册表持久化 (JSON)                    │
│  · write_file    写文件(任意格式) │ 心跳 │  7 个工具:                             │
│  · sys_info      系统信息        │◀─────│  nodes_list / node_call /              │
│  · send_file  本机文件写入节点    │      │  nodes_run / nodes_fanout /            │
│  断线自动重连 (5s)               │      │  nodes_prune / send_file /             │
│                                  │      │  fetch_file                           │
└────────────────────────────────┘      └───────────────────────────────────────┘
```

**核心设计**：hub 只注册 7 个固定分发工具（nodes_list / nodes_prune / node_call / nodes_run / nodes_fanout / send_file / fetch_file），设备注册是**数据**（注册表）而不是工具定义 —— 新设备上线后**无需重启 Hermes**，`nodes_list` 立即可见、`node_call` 立即可调。

## 快速开始

### 1. 安装 hub 插件（跑 Hermes 的那台机器）

```bash
# 直接从 git 仓库安装（hub 在仓库的 hub/hermes_node_hub 子目录）
hermes plugins install https://github.com/LKJacky/hermes_nodes.git#hub/hermes_node_hub

# 可选：配置 token 和端口（强烈建议配 token）
cat >> ~/.hermes/.env <<'EOF'
HERMES_NODE_HUB_TOKEN=换成你的强随机密钥      # openssl rand -hex 32
HERMES_NODE_HUB_PORT=9721
EOF

# 重启 Hermes —— 之后对话里就能用 nodes_list 等工具了
```

> 仓库方式安装会拷贝到 `~/.hermes/plugins/`；升级用 `hermes plugins update hermes-node-hub`（或重新 install --force）。
> 插件会在**第一次调用工具时**才启动服务器（懒加载），不占资源。

### 2. 在每台设备上装 node

```bash
# 直接从 git 仓库安装（node 包在仓库的 node/ 子目录）
pip install "hermes-node @ git+https://github.com/LKJacky/hermes_nodes.git#subdirectory=node"

# 启动（前台试跑）
hermes-node --hub http://hub-host:9721 --token <TOKEN> --device macbook
```

后台常驻（选一个）：

- **macOS (launchd)**：`docs/install.md` 里有现成 plist
- **Linux (systemd)**：`docs/install.md` 里有现成 unit
- 或者最简单：`nohup hermes-node --hub ... --token ... &`

### 3. 用起来

在 Hermes 对话里（工具注入到所有平台）：

```
nodes_list                      → 查看所有已注册设备 + 在线状态（自动清理过期离线设备）
nodes_prune                     → 手动清理离线设备（ttl=0 立即清掉所有离线）
nodes_run device=macbook command="df -h"   → 在 macbook 上执行命令
node_call device=devbox tool=read_file args={"path": "/etc/hosts"}
send_file path=/data/x.py content=/local/script.py   → 把本机文件写入节点（content 默认是本地路径，hub 读取后经内部 WS 传输，不经过 LLM 工具参数；传 extra={"path_replace": []} 则 content 按内联文本处理；任意格式：文本直传，二进制自动 base64）
fetch_file path=/data/x.py dest=/local/x.py   → 把节点文件拉回本机（hub 经内部 WS 读节点内容后写入本地 dest，同样不经过 LLM 工具参数；任意格式：文本直写，二进制自动 base64 解码）
nodes_fanout tool=sys_info      → 所有在线设备的系统信息一把梭
```

### 热插拔（按需连接，用完即走）

节点**不需要长期驻留** —— 按需启动、空闲自动退出、hub 自动清理：

```bash
# 连接后 60 秒内没有任务就自动断开退出（适合脚本/定时调用场景）
hermes-node --hub http://hub-host:9731 --token <TOKEN> --device gpu_dev --idle-timeout 60

# 断开后不再重连（配合 idle-timeout 组成"用完即走"）
hermes-node --hub ... --device gpu_dev --idle-timeout 60 --once
```

hub 端配套行为（无需任何操作）：
- 设备断开连接 → **立即标记离线**（不用等心跳超时）
- 离线超过 `HERMES_NODE_HUB_OFFLINE_TTL`（默认 300s）→ 下次 `nodes_list`/`nodes_prune` 时自动从注册表删除
- 设备随时重新连接 → 自动恢复 online，无需任何配置（真正的即插即用）

## 配置项（环境变量）

| 变量 | 默认 | 说明 |
|---|---|---|
| `HERMES_NODE_HUB_HOST` | `127.0.0.1` | hub 绑定地址 |
| `HERMES_NODE_HUB_PORT` | `9721` | hub 端口 |
| `HERMES_NODE_HUB_TOKEN` | 空 | 共享密钥；为空时仅建议 loopback 部署 |
| `HERMES_NODE_HUB_REGISTRY_FILE` | `~/.hermes/node-hub-registry.json` | 注册表持久化路径 |
| `HERMES_NODE_HUB_HEARTBEAT_TIMEOUT` | `45` | 心跳超时（秒），超时判离线 |
| `HERMES_NODE_HUB_OFFLINE_TTL` | `300` | 离线设备保留时长（秒），过期自动清理（热插拔） |
| `HERMES_NODE_HUB_CALL_TIMEOUT` | `120` | 单次调用默认超时（秒） |
| `HERMES_NODE_HUB_SEND_FILE_MAX_BYTES` | `67108864` | send_file 读取本地文件的大小上限（字节，文本或二进制），超限提示改用 exec_command 脚本（如 curl） |

node 端：`HERMES_NODE_HUB`（hub URL）、`HERMES_NODE_TOKEN`、`HERMES_NODE_DEVICE`（设备名，默认主机名）、`HERMES_NODE_IDLE_TIMEOUT`（空闲自动断开秒数，0=常驻）。

## 安全

- 每个请求/连接都要校验共享 token（`secrets.compare_digest` 恒定时间比较）
- hub 默认只绑 `127.0.0.1` —— 远程设备请走 **Tailscale** 或 **SSH 隧道**（见 `docs/install.md`），不要直接暴露公网
- 每台设备一个 token 粒度可做到最小权限（想细粒度就每台设备跑一个独立 hub 端口）

## 目录结构

```
hub/hermes_node_hub/   Hermes 插件（server / registry / 6 个工具）
node/                  设备端 Python 包（pip 安装）
docs/protocol.md       注册与调用协议规范
docs/install.md        systemd / launchd / Tailscale 部署
examples/node-config.yaml  配置示例
smoke_test.py          端到端冒烟测试（python3 smoke_test.py）
```

## 协议速览（详见 docs/protocol.md）

```
注册:  node → hub  WS /ws?token=...  hello {device, platform, capabilities}
心跳:  node → hub  {"type":"heartbeat"}               每 15s
调用:  hub  → node  {"type":"call","id","tool","args"} 
结果:  node → hub  {"type":"result","id","ok","output"}
```

## 许可证

MIT
