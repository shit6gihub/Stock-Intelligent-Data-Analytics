# Changelog

本文件记录项目中每一次可提交变更。新记录按日期倒序添加，并归入 `fix`、`feature`、`update` 或 `doc` 类别。

## 2026-08-10

### fix

- 修复 PushPlus 设置页重复测试固定文案时被服务端判定为验证错误的问题；测试消息现带有唯一时间与编号，并提供更明确的失败提示。
- 修复右下角 Chat 助手未解析 GFM Markdown 的问题，现可渲染表格、代码块、列表和链接，并为窄屏表格提供横向滚动。
- 修复 PushPlus 渠道可能只留站内通知的问题：开启 `info` 外发、严格验证渠道配置和 API 回执，并在设置保存时自动发送测试消息。
- 修复预测容器内 PanWatch 地址和认证硬编码，改用 `PANWATCH_URL` 与数据库签发的短时 Token；同时将预测历史持久化到 `panwatch_forecast_data` 数据卷。
- 让 Docker Compose 预测引擎直接读取 PanWatch 数据库中的 LLM 配置，移除不适用于容器部署的 systemd 同步提示。

### feature

- 新增电脑 Web 推送：用户可在设置页授权并测试浏览器系统通知，页面打开或在后台运行时会将新站内消息去重后推送到电脑。

### update

- 无。

### doc

- 建立 `CHANGELOG.md` 及每次可提交变更都必须同步记录的开发规则。

## 2026-08-09

### fix

- 无。

### feature

- 新增 `dev-0.1.1` 本地 Docker 开发环境，同时运行 PanWatch 主服务和预测引擎，并复用已有 `panwatch_data` 数据卷。

### update

- 无。

### doc

- 无。
