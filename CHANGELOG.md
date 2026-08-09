# Changelog

本文件记录项目中每一次可提交变更。新记录按日期倒序添加，并归入 `fix`、`feature`、`update` 或 `doc` 类别。

## 2026-08-10

### fix

- 修复预测容器内 PanWatch 地址和认证硬编码，改用 `PANWATCH_URL` 与数据库签发的短时 Token；同时将预测历史持久化到 `panwatch_forecast_data` 数据卷。
- 让 Docker Compose 预测引擎直接读取 PanWatch 数据库中的 LLM 配置，移除不适用于容器部署的 systemd 同步提示。

### feature

- 无。

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
