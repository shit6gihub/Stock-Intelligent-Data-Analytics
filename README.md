# 数智分析 (Stock-Intelligent-Data-Analytics)

**自托管 AI 盯盘助手 · 集成 [TradingAgents](https://github.com/TauricResearch/TradingAgents) 多 Agent 投资决策** — A 股 / 港股 / 美股实时监控、持仓管理、智能分析、全渠道推送

[![GitHub stars](https://img.shields.io/github/stars/xiaoze-hub/Stock-Intelligent-Data-Analytics?style=flat&logo=github&color=yellow)](https://github.com/xiaoze-hub/Stock-Intelligent-Data-Analytics/stargazers)
[![GHCR Package](https://img.shields.io/badge/ghcr-stock--intelligent--data--analytics-2496ED?logo=docker&logoColor=white)](https://github.com/xiaoze-hub/Stock-Intelligent-Data-Analytics/pkgs/container/stock-intelligent-data-analytics)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Last commit](https://img.shields.io/github/last-commit/xiaoze-hub/Stock-Intelligent-Data-Analytics)](https://github.com/xiaoze-hub/Stock-Intelligent-Data-Analytics/commits/main)
[![PWA](https://img.shields.io/badge/PWA-installable-5A0FC8?logo=pwa&logoColor=white)](https://github.com/xiaoze-hub/Stock-Intelligent-Data-Analytics)

![数智分析 · TradingAgents 深度分析演示](docs/screenshots/tradingagents-demo.gif)

> 🧠 **持仓页点一下 → TradingAgents 9-Agent 投研团队接力分析 → 看多看空辩论 → 风控审查 → PM 决策书,3-5 分钟一条完整推理链,结论直推到你的 IM。**

## 📸 功能一览

| 持仓 · 多账户汇总 | 机会页 · AI 评分选股 |
|:---:|:---:|
| ![持仓管理](./docs/screenshots/portfolio.png) | ![机会页 AI 评分](./docs/screenshots/opportunities.png) |
| **模拟盘 · 净值曲线 + 绩效** | **个股深度详情** |
| ![模拟盘](./docs/screenshots/papertrading.png) | ![个股详情](./docs/screenshots/stock-detail.png) |
| **技术指标共振 · 一眼 MACD/RSI/KDJ** | **价格提醒 · 条件组合触发** |
| ![技术指标](./docs/screenshots/technicals.png) | ![价格提醒](./docs/screenshots/alerts.png) |

<details>
<summary>移动端截图</summary>

<img src="./docs/screenshots/mobile.png" width="300" /> <img src="./docs/screenshots/mobile-detail.png" width="300" />

> 📱 支持 PWA，移动端可「添加到主屏幕」当原生 App 用。

</details>

> 💡 如果数智分析对你有帮助，点右上角 ⭐ **Star** 支持一下 —— 这是对开源项目最好的鼓励，也能让更多人发现它。

## 🧠 深度分析：TradingAgents 多 Agent 决策

接入 [TradingAgents](https://github.com/TauricResearch/TradingAgents)（76k+ star）多 Agent 投资决策框架，在持仓页点 🧠 图标即可触发：

- **4 类分析师**（技术 / 情绪 / 新闻 / 基本面） → **看多看空辩论** → **风控审查** → **PM 整合决策**
- 3-5 分钟输出完整推理链，结论同步推送到 Telegram / 微信 / 钉钉
- 默认 deepseek-chat，单次 ~$0.05，月度预算可控

## 为什么选择数智分析？

- **数据私有** — 自托管部署，持仓数据不经过任何第三方
- **AI 原生** — 不是简单的指标堆砌，而是让 AI 理解你的持仓、风格和目标
- **开箱即用** — Docker 一键部署，5 分钟完成配置

## 核心功能

<details>
<summary><b>智能 Agent 系统</b></summary>

| Agent | 触发时机 | 功能 |
|-------|---------|------|
| **盘前分析** | 每日开盘前 | 综合隔夜美股、新闻消息、技术形态，给出今日操作策略 |
| **盘中监测** | 交易时段实时 | 监控异动信号，RSI/KDJ/MACD 共振时推送提醒 |
| **盘后日报** | 每日收盘后 | 复盘当日走势，分析资金流向，规划次日操作 |
| **新闻速递** | 定时采集 | 抓取财经新闻，AI 筛选与持仓相关的重要信息 |

</details>

<details>
<summary><b>专业技术分析</b></summary>

- **趋势指标**：MA 多空排列、MACD 金叉死叉、布林带突破
- **动量指标**：RSI 超买超卖、KDJ 钝化与背离
- **量价分析**：量比异动、缩量回调、放量突破
- **形态识别**：锤子线、吞没形态、十字星等 K 线形态
- **支撑压力**：自动计算多级支撑位和压力位

</details>

<details>
<summary><b>多市场 & 多账户</b></summary>

- **覆盖市场**：A 股、港股、美股实时行情
- **账户管理**：支持多券商账户独立管理，汇总展示总资产
- **交易风格**：按短线/波段/长线分别设置，AI 建议更精准

</details>

<details>
<summary><b>全渠道通知</b></summary>

Telegram / 企业微信 / 钉钉 / 飞书 / Bark / 自定义 Webhook

</details>

<details>
<summary><b>价格提醒</b></summary>

- 支持价格、涨跌幅、成交额、量比等条件组合（AND / OR）
- 支持交易时段/全天生效、冷却时间、日触发上限、重复触发模式
- 到期时间使用弹窗内日期面板 + `HH:mm` 输入，留空表示永不过期
- 可按规则选择通知渠道，不选则走系统默认渠道

</details>

## 快速开始

```bash
docker run -d \
  --name panwatch \
  -p 8000:8000 \
  -v panwatch_data:/app/data \
  ghcr.io/xiaoze-hub/stock-intelligent-data-analytics:latest
```

访问 `http://localhost:8000`，首次使用设置账号密码即可。

说明：镜像内已包含 Playwright 运行所需的系统依赖；Chromium 浏览器会在容器首次启动时自动下载并安装到挂载卷（默认 `/app/data/playwright`），首次启动可能需要几分钟且需要网络可达。

如果不需要截图等浏览器能力，可以在启动容器时设置 `PLAYWRIGHT_SKIP_BROWSER_INSTALL=1` 跳过首次 Chromium 下载/安装。

<details>
<summary>Docker Compose</summary>

```yaml
version: '3.8'
services:
  panwatch:
    image: ghcr.io/xiaoze-hub/stock-intelligent-data-analytics:latest
    container_name: panwatch
    ports:
      - "8000:8000"
    volumes:
      - panwatch_data:/app/data
    restart: unless-stopped

volumes:
  panwatch_data:
```

```bash
docker-compose up -d
```

</details>

<details>
<summary>环境变量</summary>

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `AUTH_USERNAME` | 预设登录用户名 | 首次访问时设置 |
| `AUTH_PASSWORD` | 预设登录密码 | 首次访问时设置 |
| `JWT_SECRET` | JWT 签名密钥 | 自动生成 |
| `DATA_DIR` | 数据存储目录 | `./data` |
| `TZ` | 应用时区（影响 Agent 调度触发时间与时间展示） | `Asia/Shanghai` |
| `PLAYWRIGHT_SKIP_BROWSER_INSTALL` | 跳过首次 Chromium 安装（不需要截图时可用） | 未设置 |
| `LOG_LEVEL` | 控制台日志级别。默认 `INFO`（只输出业务事件 + 错误）；排查问题时设 `DEBUG` 可看到调度心跳、采集过程等底层日志。UI 日志板始终保留完整记录，不受影响 | `INFO` |
| `HTTP_PROXY` / `HTTPS_PROXY` / `http_proxy` | 出站 HTTP 代理。三种配置方式任选其一: ① 启动前 `export HTTP_PROXY=...`；② `.env` 里写 `http_proxy=http://host:port`；③ UI「设置 → 全局 HTTP 代理」。三者优先级:外部环境变量 > UI > `.env`。生效后所有 httpx 客户端走代理。`NO_PROXY` 默认包含 `localhost,127.0.0.1` | 未设置 |

</details>

<details>
<summary>首次配置</summary>

1. 访问 Web 界面，设置登录账号
2. **设置 → AI 服务商**：配置 OpenAI 兼容 API（支持 OpenAI / 智谱 / DeepSeek / Ollama 等）
3. **设置 → 通知渠道**：添加 Telegram 或其他推送渠道
4. **持仓 → 添加股票**：添加自选股，启用对应 Agent

</details>

<details>
<summary>本地开发</summary>

**环境要求**：Python 3.10+ / Node.js 18+ / pnpm

```bash
# 一键开发（推荐）
make dev-api          # 启动后端（自动 venv+依赖，监听 :8000）
make dev-web          # 启动前端（自动 pnpm install，监听 :5183）

# 或手动
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python server.py                              # 后端 :8000

cd frontend && pnpm install && pnpm dev       # 前端 :5183
```

前端 dev server 跑在 `http://localhost:5183`，并把 `/api` 代理到 `127.0.0.1:8000`。
前端用 `:5183` 而非默认 `:5173`，是为了和 BeeCount-Cloud 等本地常驻前端错开。

</details>

<details>
<summary><b>技术栈</b></summary>

**后端**：FastAPI / SQLAlchemy / APScheduler / OpenAI SDK

**前端**：React 18 / TypeScript / Tailwind CSS / shadcn/ui

</details>

<details>
<summary><b>发布（Docker 镜像）</b></summary>

本项目内置 GitHub Actions 发布流程：

- 打 tag（例如 `0.2.3`）会自动构建并推送 Docker 镜像
  - `ghcr.io/xiaoze-hub/stock-intelligent-data-analytics:0.2.3`
  - `ghcr.io/xiaoze-hub/stock-intelligent-data-analytics:latest`
- 也支持在 GitHub Actions 里手动触发（workflow_dispatch）指定版本号

需要在仓库 Secrets 中配置（发布到 GitHub Container Registry）：

- `DOCKERHUB_USERNAME`
- `DOCKERHUB_TOKEN`

</details>

## 🚀 新增核心功能（本 fork 定制）

基于上游 PanWatch 深度定制，新增以下能力（均已合入 `ghcr.io/xiaoze-hub/stock-intelligent-data-analytics` 镜像）：

### 1. 四模型融合预测引擎（8010 独立服务）
- **模型**：XGBoost / 线性回归 / Kronos（时序）/ Lag-Llama（长期）四模型并行预测个股价格
- **质量修复**：自动剔除离谱外推（±40% 截断）、权重/置信度按方向一致率动态回算（不再写死 100%）
- **LLM 情绪总结**：基于公告/板块/资金面，输出自然语言点评（如「连续发布异常波动公告 → 监管风险」）
- **企微推送美化**：专用 `generate_wecom_report()`，emoji 分段 + 分隔线 + 四模型逐行 + 去 JSON，手机友好

### 2. 主力资金面融合（关键特征）
- **数据源**：东方财富标准口径（超大单+大单净买入），经 8000 主后端 `tdx ask`（问小达）接口注入 8010
- **参与决策**：计算 `capital_score`（连续净流入天数 / 合计力度 / 当日方向 → -1~+1），联动模型权重与策略合成
- **展示**：近 5 日主力净流入趋势 + 对策略影响结论（偏多确认看多 / 偏空下调）

### 3. 龙虎榜游资信号
- **数据源**：`marketdata` 包 ftshare vendor（经 8000 `/api/market-data/dragon-tiger` 代理，Key 实时来自 UI）
- **展示**：当日上榜明细 + 全市场情绪 + 游资净买合计，作为短线博弈特征

### 4. 数据源 Key 全经 UI 维护
- 所有 key（zhitu / wudao / tdx / itick / ftshare 等）在「设置 → 接口Key」配置，运行时从 DB 读取，国内外通用

## 🔌 接口与 Key 获取地址

| 数据源 | 用途 | Key 获取地址 |
|--------|------|--------------|
| **通达信 / 问小达 (tdx)** | 行情/K线/资金流(东财口径)/选股 | 通达信开放平台 / 问小达开放 API（项目内 `src/tdx_mcp/`）
| **悟道 (wudao) MCP** | A股实时数据/龙虎榜/情绪/公告 | [wudao 开放平台](https://wudao.com) 申请 `WUDAO_MCP_TOKEN`
| **智兔 (zhitu) MCP** | 资金流/公告/概念板块 | [智兔数科](https://zhitu.com) 申请 `ZHITU_TOKEN`
| **itick** | 实时/历史行情补充 | [itick API](https://itick.org) 申请 `ITICK_TOKEN`
| **ftshare (marketdata)** | 龙虎榜/北向等 | 经 8000 代理，Key 在「设置 → 接口Key」配置
| **LLM (情绪打分)** | 8010 预测引擎情绪点评 | OpenAI 兼容端点（默认 `https://api.agnes-ai.cn/v1`，模型 `agnes-2.5-flash`）

> 所有 Key 在 Web UI「设置 → 接口Key」统一维护，无需硬编码，改后实时生效。

## 📦 版本

- **当前版本**：`v0.1.2`（镜像 tag 与 GitHub Release 对齐）
- **容器镜像**：`ghcr.io/xiaoze-hub/stock-intelligent-data-analytics:latest`（已公开，匿名可拉）
- **更新日志**：见 [Releases](https://github.com/xiaoze-hub/Stock-Intelligent-Data-Analytics/releases)
- 打 tag（如 `v0.1.3`）触发 GitHub Actions 自动构建并推送镜像到 GHCR

## 贡献

欢迎提交 Issue 和 PR！自定义 Agent 和数据源开发请参考 [贡献指南](CONTRIBUTING.md)。
社区交流（Telegram）：[t.me/panwatch](https://t.me/panwatch)

## License

[MIT](LICENSE)
