# 部署指南

## 网络拓扑（推荐）

```
Mac (Hermes Desktop + hub 插件)
        │  Tailscale 100.x.x.1
        ├── devbox   (Linux)   hermes-node --device devbox
        ├── macbook  (macOS)   hermes-node --device macbook
        └── gpu-box  (Linux)   hermes-node --device gpu-box
```

hub 只绑 `127.0.0.1`。远程设备通过 **Tailscale**（推荐）或 **SSH 隧道** 访问。

### Tailscale（推荐）

1. 所有设备装 Tailscale 并登录同一账号
2. hub 机器保持 `HERMES_NODE_HUB_HOST=127.0.0.1`
3. 各设备用 Tailscale IP 作为 hub 地址：`hermes-node --hub http://100.x.x.1:9721`
   （Tailscale 自带加密，token 只作为第二道防线）

### SSH 隧道（无 Tailscale 时）

在**每台设备**上建一条到 hub 机器的隧道：

```bash
# 设备上执行（hub 机器需要能 ssh 进来）
ssh -N -L 9721:127.0.0.1:9721 user@hub-machine &
# 然后连本机即可
hermes-node --hub http://127.0.0.1:9721 --token <TOKEN> --device devbox
```

## 开机自启

### Linux (systemd) —— 每台设备

`/etc/systemd/system/hermes-node.service`：

```ini
[Unit]
Description=hermes-node (device client for hermes-nodes)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment=HERMES_NODE_HUB=http://100.x.x.1:9721
Environment=HERMES_NODE_TOKEN=<TOKEN>
Environment=HERMES_NODE_DEVICE=devbox
ExecStart=/usr/local/bin/hermes-node
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now hermes-node
sudo systemctl status hermes-node
```

### macOS (launchd)

`~/Library/LaunchAgents/com.hermes.node.plist`：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.hermes.node</string>
    <key>ProgramArguments</key>
    <array>
        <string>/opt/homebrew/bin/hermes-node</string>
        <string>--hub</string><string>http://100.x.x.1:9721</string>
        <string>--token</string><string>REPLACE_TOKEN</string>
        <string>--device</string><string>macbook</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>/tmp/hermes-node.log</string>
    <key>StandardErrorPath</key><string>/tmp/hermes-node.err</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.hermes.node.plist
```

## 日常巡检（可选，配合 Hermes cron）

在 Hermes 里建一个每日任务：`nodes_fanout tool=sys_info`，把结果汇总成健康报告推给你 —— 磁盘、内存、GPU 一目了然，异常设备一眼看出。

## 升级

```bash
git -C <repo> pull
pip install --upgrade ./node          # 每台设备
cp -r hub/hermes_node_hub ~/.hermes/plugins/hermes-node-hub   # hub 机器
```
