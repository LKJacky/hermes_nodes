# hermes-nodes 协议规范 v0.1

hub 暴露两个端点；所有帧均为 JSON。

## 端点

| 端点 | 用途 |
|---|---|
| `POST /register` | 可选预检：校验 token，返回 node_id（调试/管理用） |
| `GET /status` | 当前注册表快照（调试用） |
| `WS /ws?token=...` | 节点长连接：注册、心跳、调用、结果 |

## 鉴权

- 所有请求必须携带共享 token：`POST /register` body 的 `token` 字段，`WS` 的 query 参数
- token 校验使用 `secrets.compare_digest`（恒定时间比较，防时序攻击）
- 未配置 token（`HERMES_NODE_HUB_TOKEN` 为空）时放行 —— 仅建议 loopback 部署

## 连接生命周期

1. 节点 `WS /ws?token=...` 握手
2. 节点发送 hello（**这步即注册**，可反复发送以更新能力）：
   ```json
   {"type":"hello","device":"macbook","platform":"macOS-15.0","capabilities":["exec_command","read_file","write_file","sys_info"]}
   ```
3. 节点每 15s 发送心跳；hub 记录 `last_seen`
4. **断开即时离线**：WS 断开时 hub 立即标记 `disconnected_at`，`nodes_list` 立刻显示 offline（无需等心跳超时）
5. **自动清理（热插拔）**：离线超过 `HERMES_NODE_HUB_OFFLINE_TTL`（默认 300s）的设备，在下次 `nodes_list` / `nodes_prune` 时自动从注册表删除
6. 断线重连：节点 5s 退避重连，重连后重新 hello；hub 侧 `_conns` 清理旧连接、`disconnected_at` 复位

### 热插拔节点（可选）

节点支持 `--idle-timeout N`：连接后 N 秒内没有收到任何 call 就主动断开并退出（配合 `--once` 形成"用完即走"）。适合按需调用的场景 —— 节点不需要长期驻留，hub 会自动清理它的离线记录。

## 调用协议

```
hub → node: {"type":"call","id":"<hex>","tool":"exec_command","args":{"cmd":"df -h"},"timeout":120}
node → hub: {"type":"result","id":"<hex>","ok":true,"output":{...}}
node → hub: {"type":"result","id":"<hex>","ok":false,"error":"..."}
```

- `id`：调用方生成的唯一 hex（hub 用 uuid4）
- `timeout`：给节点执行的建议超时（节点侧 `asyncio.to_thread` 不强制中断，超时由 hub 侧 `future.result(timeout)` 兜底）
- 输出：`output` 为任意 JSON 值；错误时 `error` 为人类可读字符串
- 未知帧类型：双方均忽略（前向兼容）

## 节点能力（v0.1 内置）

| 工具 | 参数 | 返回 |
|---|---|---|
| `exec_command` | `cmd`(必填), `timeout`=120, `cwd`="~" | `{code, stdout, stderr, duration_ms}` |
| `read_file` | `path`(必填), `max_bytes`=200000 | `{path, bytes, content}` 或 `{error}` |
| `write_file` | `path`(必填), `content`(必填) | `{ok, path, bytes}` 或 `{error}` |
| `sys_info` | — | `{hostname, platform, cpu_count, load_avg, mem_*, disk_free_gb, gpu, ts}` |

扩展工具：在 `node/hermes_node/tools.py` 加函数并加入 `ALL_TOOLS`，capabilities 自动带上。
