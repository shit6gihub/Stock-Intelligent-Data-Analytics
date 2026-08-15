<div align="center">

# SIDA · 数智分析

**开源的 A 股 AI 投研终端** —— 行情数据 → AI 分析 → 四模型预测 → 到期验证 → 微信推送,一条全 AI 链路,每个环节可追溯,完全自部署。

[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](LICENSE)
![Version](https://img.shields.io/badge/version-v0.2.41-green)
[![Docker](https://img.shields.io/badge/Docker-ghcr.io-blue?logo=docker)](https://github.com/xiaoze-hub/Stock-Intelligent-Data-Analytics/pkgs/container/stock-intelligent-data-analytics)

*语言: [English](README.md) · [中文](README.zh-CN.md)*

![SIDA 演示](docs/screenshots/demo.gif)

</div>

---

## 为什么是 SIDA?

市面上的工具只给你**数据**。SIDA 把闭环做完整:**读懂市场 → 分析 → 预测 → 验证自己的预测 → 在微信里跟你对话** —— 全链路 AI 参与,每步可追溯。

- 🔍 **主力意图分析(逐笔级)** — 吸筹/派发识别、拆单伪装识别、托盘/出货博弈,复刻同花顺暗盘口径
- 🔮 **四模型预测 + 验证闭环** — Kronos + Chronos-Bolt + XGBoost + 线性回归加权投票,**权重按历史命中率动态调整**,每条预测到期自动对照实际行情(hit/miss + 实际涨跌)
- 🤖 **AI 对话助手(19+ 数据工具)** — 流式输出,图片/文件/链接多模态,互动易官方问答、热榜、异动池、竞价盘口
- 📱 **微信双向对话** — 腾讯官方 iLink 协议扫码绑定个人微信,手机上直接问个股、收推送
- 📊 **自动报告** — 盘前(8:30)/盘后(15:30)真实数据 + AI 解读,cron 定时生成归集
- 🏆 **事件驱动机会发现** — 7 大策略信号、异动池、热榜、题材启动识别 —— **提前发现题材,不追涨停池**

## 界面截图

| 首页 | 预测 & 到期验证 | 机会页 |
|---|---|---|
| ![首页](docs/screenshots/home.png) | ![预测](docs/screenshots/forecast.png) | ![机会](docs/screenshots/opportunities.png) |

## 快速开始

### Docker(推荐)

```bash
# GitHub 源(全球)或阿里云 ACR(国内加速)
docker pull ghcr.io/xiaoze-hub/stock-intelligent-data-analytics:latest
# 或: docker pull crpi-mte80ai8o78b1429.cn-shanghai.personal.cr.aliyuncs.com/xiaozexwz/xzxwz:v0.2.41

docker run -d --name sida -p 8000:8000 --restart unless-stopped \
  -v sida_data:/app/data \
  -e AUTH_USERNAME=admin \
  -e AUTH_PASSWORD=你的密码 \
  -e TZ=Asia/Shanghai \
  ghcr.io/xiaoze-hub/stock-intelligent-data-analytics:latest
```

打开 http://localhost:8000 即可使用。

### 开发环境

```bash
# 后端
pip install -r requirements.txt
python server.py

# 前端
cd frontend && pnpm install && pnpm dev
```

## 技术架构

```
┌─────────────┐   ┌──────────────┐   ┌──────────────┐   ┌────────────┐
│  行情数据源   │ → │ AI 分析层     │ → │  预测引擎     │ → │  报告中心   │
│ 腾讯/东财/   │   │ 主力意图/技术面│   │ 4模型+AI裁判 │   │ 盘前/盘后   │
│ 同花顺/竞价  │   │ 多智能体辩论   │   │ 权重动态调整  │   │ 自动生成     │
└─────────────┘   └──────────────┘   └──────────────┘   └────────────┘
        │                 │                  │                  │
        └───────── AI 对话助手「数智分析BOT」 ←┘                  │
                        │ 图片/文件/链接多模态                     │
                        ▼                                        │
                ┌─────────────┐                                 │
                │ 微信推送/对话 │ ←────────────────────────────────┘
                │ iLink 直连   │
                └─────────────┘
```

| 层 | 技术 |
|---|---|
| 后端 | Python FastAPI + SQLAlchemy + SQLite(WAL) + APScheduler |
| 前端 | React 18 + Vite + Tailwind + ECharts |
| 预测引擎 | Kronos / Chronos-Bolt(时序基础模型)+ XGBoost + 线性回归 |
| 微信通道 | 腾讯官方 iLink Bot API(纯 Python 直连,扫码绑定) |
| AI 配置 | 统一 LLM 配置中心(多服务商 + 7 场景绑定) |

## 开源版 vs 专业版

| | 开源版(本仓库) | 专业版(付费) |
|---|---|---|
| 许可证 | AGPL-3.0 | 闭源 |
| 核心:行情/分析/预测/对话 | ✅ | ✅ |
| 全部功能/高级模型 | 部分 | 全部 |
| 托管云服务/优先支持 | — | ✅ |

开源版完全可自部署、免费使用。专业版解锁全部功能,适合不想自己维护的用户。

## 多用户 & AI 配置

- **账号隔离**:持仓、自选、通知渠道、微信绑定均按用户独立
- **统一 LLM 配置中心**:OpenAI 兼容服务商、模型池、7 场景绑定(对话/报告/裁判/自检/评分/深度分析/视觉代理)
- **通知渠道**:个人微信(iLink 扫码)/ 企业微信 / PushPlus / Server酱 / 邮件

## 数据源

腾讯 / 东财 / 同花顺 / 新浪 / 通达信(问小达)/ 巨潮(互动易)——行情、K线、分时、资金流、逐笔成交、集合竞价、涨停池、热榜、异动池、龙虎榜、两融、股东、分红。

## 免责声明

本项目仅供技术研究和学习使用,所有 AI 生成的分析、预测、报告仅供参考,**不构成任何投资建议**。股市有风险,投资需谨慎。

---

## 赞助 Sponsor

如果数智分析对你有帮助,欢迎请作者喝杯咖啡 ☕ 你的支持是持续维护的动力!

| 方式 | 入口 |
|:---:|:---:|
| **微信赞赏** | <img src="./assets/sponsor-wechat.png" width="200" alt="微信赞赏码" /> |

> 提示:点右上角 ⭐ **Star** 支持项目。
