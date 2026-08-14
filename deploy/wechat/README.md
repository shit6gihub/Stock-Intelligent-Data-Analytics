# SIDA 个人微信推送通道(OpenClaw 宿主机部署)

SIDA 的"个人微信推送"依赖微信官方的 iLink 通道(扫码授权),官方客户端实现是 OpenClaw 的微信插件。
**OpenClaw 作为宿主机外部服务部署**(类似数据库),SIDA 容器通过本地桥接(127.0.0.1:8001)调用,镜像保持纯净。

## 架构

```
[SIDA 容器 8000] --HTTP--> 宿主机 bridge(127.0.0.1:8001) --CLI--> OpenClaw 网关(18789) --iLink--> 微信
         |                          |
    扫码绑定 API                 wechat_bridge.py
   (wechat_bind.py)            (post /start /status /send)
```

## 宿主机部署(一次性,Ubuntu 22.04+)

```bash
# 1. Node 24
curl -fsSL https://deb.nodesource.com/setup_24.x | sudo -E bash -
sudo apt-get install -y nodejs

# 2. OpenClaw + 微信官方插件
sudo npm install -g openclaw@latest
npx -y @tencent-weixin/openclaw-weixin-cli install   # 会触发扫码, 扫一次绑定管理员微信

# 3. 网关常驻
loginctl enable-linger $USER
openclaw gateway install && openclaw gateway start

# 4. 微信桥接服务(systemd)
sudo cp deploy/wechat/wechat_bridge.py /home/ubuntu/wechat_bridge.py
sudo tee /etc/systemd/system/wechat-bridge.service > /dev/null << 'EOF'
[Unit]
Description=WeChat bridge for SIDA
After=network.target

[Service]
User=ubuntu
ExecStart=/usr/bin/python3 /home/ubuntu/wechat_bridge.py
Restart=always

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload && sudo systemctl enable --now wechat-bridge
```

## SIDA 容器配置

```bash
docker run ... -e SIDA_WECHAT_BRIDGE=http://172.17.0.1:8001 ...
```

`SIDA_WECHAT_BRIDGE` 默认就是 `http://172.17.0.1:8001`(容器访问宿主机 bridge),一般无需显式设置。

## 多用户

每个用户在 SIDA 设置页 → 通知渠道 → 「OpenClaw 个人微信」→ 扫码绑定自己的微信:
- `POST /api/notify/wechat-bind/start` → 起 `openclaw channels login --account <bind_id>` → 返回二维码
- 扫码后手机确认「将新的 OpenClaw 连接到微信」→ 轮询 status → 自动保存渠道(按 user_id 隔离)
- 一个微信账号只能绑一个 OpenClaw 实例(官方限制);绑过其他实例需先解绑

## 踩坑

- 微信账号必须能访问 `ilinkai.weixin.qq.com`(国内服务器无障碍;海外节点可能失败)
- 只能给**建立过会话**的联系人推送(iLink context_token 机制),扫码后先在微信里给 bot 发一条消息
- 凭证存 `~/.openclaw/openclaw-weixin/accounts/`,服务器迁移时一起备份
- OpenClaw 不配 LLM 也没关系(纯当消息通道,SIDA 不依赖它的 AI 能力)
