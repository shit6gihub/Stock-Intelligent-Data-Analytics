# SIDA 交易策略审计报告

> 审计日期: 2026-08-25 | 仓库: /home/ubuntu/sida-src

---

## 一、现有策略清单

### 1.1 YAML 策略库 (6 个, 策略层过滤+排序)

定义在 `strategies/panwatch_strategies.yaml`，规则驱动，用于候选池筛选和排序。

| 策略名 | 类型 | 核心条件 | 文件:行号 |
|--------|------|----------|----------|
| **dual_low** (双低选股) | 价值 | PE<15, PB<2, 换手率>0.5%, 市值50-3000亿 | `panwatch_strategies.yaml:10-33` |
| **capital_heat** (资金热度) | 动量 | 涨幅1-9.5%, 量比>1.5, 换手率>2% | `panwatch_strategies.yaml:35-53` |
| **volume_breakout** (放量突破) | 动量 | 涨幅2-9.9%, 量比>2.0, 换手率>1.5% | `panwatch_strategies.yaml:55-72` |
| **oversold_reversal** (超卖反弹) | 反转 | 跌幅-7~+1%, 缩量量比<1.5, 换手率>0.3% | `panwatch_strategies.yaml:74-91` |
| **momentum_quality** (动量+质量) | 多因子 | 涨幅1-7%, 量比>1.0, 换手率>1% | `panwatch_strategies.yaml:93-112` |
| **low_volatility_quality** (低波防御) | 质量 | 涨跌幅±3.5%, 换手率0.5-5% | `panwatch_strategies.yaml:114-133` |

**数据时效性**: `data_completeness` 段 (yaml:136-156) 标注 dual_low 仅可用于盘后，其余 5 个盘中实时可用。

### 1.2 程序化策略目录 (7 个, 引擎层信号)

定义在 `src/core/strategy_catalog.py:24-81`，用 `StrategySpec` 数据类注册，持久化到 DB `StrategyCatalog` 表。

| 策略代码 | 名称 | 风险等级 | 默认权重 | 持有期(日) | 文件:行号 |
|----------|------|----------|----------|-----------|----------|
| trend_follow | 趋势延续 | medium | 1.15 | 5 | `strategy_catalog.py:25-32` |
| macd_golden | MACD金叉 | medium | 1.10 | 3 | `strategy_catalog.py:33-40` |
| volume_breakout | 放量突破 | high | 1.18 | 3 | `strategy_catalog.py:41-48` |
| pullback | 回踩确认 | low | 1.05 | 5 | `strategy_catalog.py:49-56` |
| rebound | 超跌反弹 | high | 0.95 | 3 | `strategy_catalog.py:57-64` |
| watchlist_agent | Agent建议 | medium | 1.00 | 3 | `strategy_catalog.py:65-72` |
| market_scan | 市场扫描 | medium | 1.08 | 3 | `strategy_catalog.py:73-81` |

### 1.3 Agent 驱动的策略 (LLM 信号)

| Agent | 功能 | 策略信号 | 文件 |
|-------|------|---------|------|
| **intraday_monitor** | 盘中实时监控 | 主力意图+暗盘+拉升分析+异常检测 | `src/agents/intraday_monitor.py` |
| **premarket_outlook** | 盘前展望 | 隔夜信息+竞价+开盘预测 | `src/agents/premarket_outlook.py` |
| **daily_report** | 收盘复盘 | 当日回顾+明日展望 | `src/agents/daily_report.py` |
| **news_digest** | 新闻速递 | 新闻情绪+事件驱动 | `src/agents/news_digest.py` |
| **auction_review** | 竞价复盘 | 竞价异动+主力意图 | `src/agents/auction_review.py` |
| **theme_launch_detector** | 题材启动检测 | 题材爆发+龙头识别 | `src/agents/theme_launch_detector.py` |
| **stock_attribution** | 个股归因 | 短线异动归因 | `src/agents/stock_attribution.py` |

### 1.4 TradingAgent 多智能体预设 (8 个)

| 预设 | 用途 | 文件:行号 |
|------|------|----------|
| sector_rotation_team | 板块轮动 | `presets/sector_rotation_team.yaml` |
| event_driven_task_force | 事件驱动 | `presets/event_driven_task_force.yaml` |
| technical_analysis_panel | 技术分析 | `presets/technical_analysis_panel.yaml` |
| investment_committee | 投资决策 | `presets/investment_committee.yaml` |
| factor_research_committee | 因子研究 | `presets/factor_research_committee.yaml` |
| risk_committee | 风控 | `presets/risk_committee.yaml` |
| value_investing_committee | 价值投资 | `presets/value_investing_committee.yaml` |
| earnings_research_desk | 业绩研究 | `presets/earnings_research_desk.yaml` |

---

## 二、信号逻辑详解

### 2.1 信号合成流水线

```
[通行情/资金流/新闻] → SignalPackBuilder (信号包) → EntryCandidate (候选池) → 
Strategy Engine (因子分解+组合约束) → StrategySignalRun (输出信号)
```

**信号包采集** (`src/core/signals/signal_pack.py`):
- `SignalPackBuilder.build_for_symbols()` 采集 6 维数据: 行情、技术(K线摘要)、持仓、新闻、资金流、事件 (line 116-497)
- 数据源策略: 腾讯行情优先, 东方财富资金流+事件, 新浪资金流兜底 (line 143-156)
- 缓存层: 内存 dict 缓存, 单次运行内避免重复接口调用 (line 59-68)

**因子分解** (`src/core/strategy_engine.py:780-925`):
- `_compute_factor_breakdown()` 将候选信号拆解为 7 个因子:
  - `alpha_score` (选股α): base_score 归一化 + 相对强度 (line 808-810)
  - `catalyst_score` (催化): 市场池来源/涨跌幅/关键词/事件分数/相对强度 (line 812-831)
  - `quality_score` (计划质量): plan_quality 归一化 + 新闻事件量 (line 833-835)
  - `risk_penalty` (风险): 高风险信号/大幅波动/低质量/负事件偏压 (line 837-847)
  - `crowd_penalty` (拥挤度): 大涨/量比过高/换手率过高/截面拥挤 (line 849-856)
  - `source_bonus` (来源加成): 特定Agent/策略代码/强相对强度 (line 858-864)
  - `regime_multiplier` (市场体制调节): 多头/空头/震荡 置信度调节 (line 866-874)

**组合约束** (`strategy_engine.py:712-777`):
- `_apply_portfolio_constraints()`: 按市场限制未持仓机会数(CN:30, HK:20, US:20)、高风险比例上限(CN:35%)、单策略集中度上限(42%) (line 204-216)

### 2.2 因子权重自校准 (M2 闭环)

**因子权重层** (`src/core/factor_weights.py:22-28`):
- 可标定 5 个因子: alpha_score, catalyst_score, quality_score, risk_penalty, crowd_penalty
- 默认权重 1.0, 支持 pin 锁定

**因子评估** (`src/core/factor_eval.py:71-80`):
- `evaluate_factor_ic()`: 用 Spearman 秩相关算因子 IC/IR
- 默认 90 天回看, 5 日持有期, 最少 20 样本

**自校准** (`src/core/factor_calibration.py:60-151`):
- `calibrate_factor_weights()`: IC/IR → 目标权重 → EMA 平滑 → clamp[0.5, 1.5]
- 惩罚因子(risk/crowd) IC 预期为负, 翻符号处理 (line 47-48)
- 每日调度, 仅未 pin 且 auto_calibrate=True 的因子参与调整

### 2.3 市场体制检测

`strategy_engine.py:365-391`:
- `_classify_market_regime()`: 基于广度(上涨占比 45%)、平均涨跌幅(30%)、活跃率(25%) → 多头/震荡/空头
- 置信度 = clamp(abs(score)×1.45 + 0.15, 0, 1)

---

## 三、回测情况

### 3.1 回测基础设施

| 组件 | 状态 | 文件:行号 |
|------|------|----------|
| **事件式回测引擎** | ✅ Phase 0, 可用 | `src/core/backtest/engine.py:92-206` |
| **成本模型** | ✅ 佣金(万2.5)+印花税(千1,卖)+滑点(0.1%) | `src/core/backtest/cost_model.py` |
| **绩效指标** | ✅ 夏普/最大回撤/胜率/盈亏比/年化收益 | `src/core/backtest/metrics.py` |
| **影子归因** | ✅ 情绪单/过早离场/过晚离场/超频交易 | `src/core/shadow_account/backtester.py:30-140` |
| **因子IC评估** | ✅ Spearman秩相关/IR/Pearson | `src/core/factor_eval.py:33-68` |
| **单元测试** | ✅ 104行, 纯合成数据, 不触发网络 | `tests/test_backtest.py` |

### 3.2 回测能力和局限

**能力**:
- 单信号回路: 信号日次日开盘入场, 逐日止损/止盈/到期平仓 (`engine.py:105-167`)
- 批量回测: 聚合净值曲线与绩效指标 (`engine.py:169-206`)
- Horizon Return: 复刻 strategy_outcome 口径用于交叉验证 (`engine.py:209-231`)
- T+1 约束: 入场日当天不可卖 (`engine.py:126-127`)

**局限**:
- 无真实历史回测流水线 (仅合成数据测试)
- 涨跌停无法成交约束未建模 (`engine.py:12` 标注 TODO)
- 无多标的并发持仓浮动 mark-to-market
- 无信号实时追踪 PnL
- 回测数据适配器(data_adapter)仅支持基础 PriceBar K线

---

## 四、支撑分析能力 (已存在但未接入策略引擎)

SIDA 有大量高质量分析模块，但未接入 `panwatch_strategies.yaml` 或 `strategy_catalog.py`:

| 模块 | 功能 | 文件 | 接入状态 | 可直接产生新策略 |
|------|------|------|----------|----------------|
| **dark_flow** | 暗盘资金流(逐笔/分价表/大中小单分层) | `src/core/dark_flow.py` | 仅 intraday_monitor 使用 | ✅ 强 |
| **chip_distribution** | 筹码分布(COST 10/50/90/筹码峰) | `src/core/chip_distribution.py` | 仅 intraday_monitor 使用 | ✅ 中 |
| **orderbook_engine** | 盘口20档演变(托单/压单/撤单/幽灵单/OB失衡) | `src/core/orderbook_engine.py` | 未接入 | ✅ 强 |
| **sentiment_cycle** | 情绪周期(冰点→修复→发酵→高潮→退潮) | `src/core/sentiment_cycle.py` | 未接入 | ✅ 强 |
| **market_mainline** | 市场主线识别(涨停池→题材聚合/打分) | `src/core/market_mainline.py` | 前端展示 | ✅ 中 |
| **commodity_rotation** | 大宗商品轮动前瞻(能源→金属→农产→黄金) | `src/core/commodity_rotation.py` | 未接入 | ✅ 中 |
| **event_catalyst_engine** | 事件驱动预期差(LLM催化推理+受益链) | `src/core/event_catalyst_engine.py` | 未接入 | ✅ 强 |
| **rally_analysis** | 盘中拉升段分析(真拉升vs出货) | `src/core/rally_analysis.py` | 仅 intraday_monitor 使用 | ✅ 中 |
| **kline_pattern** | K线形态识别(金针探底/红三兵/双响炮/上升三法) | `src/core/kline_pattern.py` | 仅 chart_analyst 使用 | ✅ 中 |
| **abnormal_moves** | 交易所异动规则监控(3日/10日/30日偏离) | `src/core/abnormal_moves.py` | 未接入 | ✅ 中 |
| **sector_filter** | 题材板块筛选(ftshare概念板块→成分股) | `src/core/sector_filter.py` | 未接入 | ✅ 中 |

---

## 五、缺口分析

### 5.1 策略类型缺口

1. **❌ 无基于资金流的预测策略** — capital_flow 数据已收集但仅用于辅助因子打分，无独立资金流预测信号
2. **❌ 无题材轮动自动化策略** — commodity_rotation + sector_filter + market_mainline 均未 YAML 化
3. **❌ 无 ML/预测模型策略** — 所有策略纯规则驱动，无 ML 预测引擎集成
4. **❌ 无盘口失衡短线策略** — orderbook_engine 的 OB 失衡/幽灵单检测未接入
5. **❌ 无情绪周期自适应策略** — sentiment_cycle 仅分析不决策
6. **❌ 无事件驱动套利策略** — event_catalyst_engine 已产出预期差信号但未入候选池
7. **❌ 无竞价策略** — auction 数据仅用于分析，未产生可执行信号
8. **❌ 无跨市场/跨品种策略** — 仅单市场单票

### 5.2 回测缺口

1. **回测未对接真实历史数据** — 仅合成数据跑单元测试
2. **无信号实时 PnL 追踪** — 引擎发出信号后无人跟踪表现
3. **无策略绩效归因分析** — 无法回答"哪个策略赚了/亏了"
4. **无参数敏感性分析** — 所有阈值(量比/换手率/PE等)均为经验值，无系统优化

### 5.3 因子层缺口

1. **因子集偏小**: 仅 5 个可标定因子，缺少动量/波动率/流动性/市值等标准因子
2. **无因子衰减分析**: IC 衰减曲线已定义但未系统分析
3. **无行业/市值中性化**: 因子未做中性化处理

---

## 六、新增策略方向 (基于 DeepSeek V4 金融量化优势)

### 方向 1: 暗盘资金流跟随策略 (Dark Flow Tracking)

**类型**: 中短线 / 资金流跟踪
**数据源**: `dark_flow.py` 的大中小单分层 + 主力意图标签
**信号逻辑**:
- 暗盘显著流入(中小单净额>阈值) + 明盘流出(大单净额<阈值) = 拆单吸筹信号 (`dark_flow.py:11-14`)
- 暗盘净额变化率 + 价格位置 → 入场时机
- 用 DeepSeek V4 推理暗盘数据的时序模式，识别主力建仓/派发

**现有证据**:
- `dark_flow.py:11-14`: "暗盘显著流入+明盘流出 = 拆单吸筹"
- `dark_flow.py:33-47`: 主力意图增强算法阈值(大单背离/量价背离/时段节奏)
- `dark_flow.py:48-54`: 数据源切换预留(L2付费接口)
- `rally_analysis.py:1-15`: 拉升段5维判别(真拉升vs出货)

**新增策略YAML示意**:
```yaml
dark_flow_tracking:
  display_name: "暗盘资金跟随"
  description: "盘中暗盘净流入异常+价格未涨 → 拆单吸筹潜伏"
  category: "capital_flow"
  filter:
    price_min: 5
    price_max: 200
    dark_flow_net_min: 800e4  # 暗盘净额>800万
    divergence: true           # 暗盘流入+明盘流出
```

### 方向 2: 情绪周期自适应策略 (Sentiment Cycle Adaptive)

**类型**: 中短线 / 情绪择时
**数据源**: `sentiment_cycle.py` 情绪周期判定 + 涨停板数据
**信号逻辑**:
- 冰点期: 首板试错策略, 轻仓过滤
- 修复期: 一进二连板策略, 小仓试探
- 发酵期: 积极做多, 聚焦核心题材
- 高潮期: 持仓格局, 减仓锁定
- 退潮期: 空仓防守, 不开新仓

**现有证据**:
- `sentiment_cycle.py:49-75`: 各周期操作提示已定义
- `sentiment_cycle.py:80-91`: 冰点期规则(涨停≤30, 连板≤3, 炸板率≥40%)
- `sentiment_cycle.py:95-100`: 修复期规则(涨停25-60, 炸板率≤35%)
- `market_mainline.py:10-38`: 市场主线识别(涨停池→题材聚合→打分)

**DeepSeek V4 优势**: 用 DeepSeek 推理判断当前周期阶段(比阈值规则更准确), 动态调整策略权重

```yaml
sentiment_adaptive:
  display_name: "情绪周期自适应"
  description: "根据短线情绪周期动态调整策略参数和仓位"
  category: "sentiment"
  regime_dependent: true
```

### 方向 3: 事件驱动预期差套利 (Event Expectation Gap Arbitrage)

**类型**: 短线 / 事件驱动
**数据源**: `event_catalyst_engine.py` 的催化信号 + 预期差判定
**信号逻辑**:
- 利好 + 预期差高 → 潜伏买入(事件未反应)
- 利好 + 预期差低 → 利好兑现, 回避
- 利空 + 预期差高 → 恐慌低吸机会
- 受益链标的联动上涨

**现有证据**:
- `event_catalyst_engine.py:244-261`: `analyze_event_catalyst()` 主入口已就绪
- `event_catalyst_engine.py:32-54`: LLM 系统提示词定义了因果链推理+预期差逻辑
- `event_catalyst_engine.py:57-69`: `build_catalyst_prompt()` 构造提示词
- `event_catalyst_engine.py:72-152`: `parse_catalyst_reply()` 解析 LLM 输出
- `event_catalyst_engine.py:213-241`: `_fetch_today_events()` 抓当日公告

**DeepSeek V4 优势**: 事件因果链推理是 DeepSeek 的强项(长上下文+逻辑推理), 可识别更复杂的传导链和预期差

```yaml
event_gap_trading:
  display_name: "事件预期差套利"
  description: "当日公告/事件的预期差信号 → 潜伏/回避决策"
  category: "event_driven"
  filter:
    expectation_gap: "high"
    event_direction: "利好"
```

### 方向 4: 盘口失衡短线狙击 (Order Book Imbalance Scalping)

**类型**: 超短线 / 盘口交易
**数据源**: `orderbook_engine.py` 的 OB 失衡 + 托单/压单/撤单/幽灵单
**信号逻辑**:
- OB 失衡 > +0.3 → 买压, 准备入场 (`orderbook_engine.py:21-22`)
- 托单增厚但价格不涨 → 承接信号, 回调买入
- 压单增厚但价格不跌 → 抛压信号, 回避
- 幽灵单(巨量堆积出现又消失) → 虚假挂单, 警惕

**现有证据**:
- `orderbook_engine.py:1-29`: 盘口演变引擎完整设计
- `orderbook_engine.py:44-56`: 阈值常量(大单/撤单/托单/幽灵单)
- `orderbook_engine.py:89-98`: `_fetch_levels()` 采集 THS L2 20档
- `orderbook_engine.py:59-60`: 尾盘自然撤单过滤(14:55后)

**DeepSeek V4 优势**: 连续盘口快照的模式识别, 区分自然撤单 vs 虚假挂单

```yaml
orderbook_imbalance:
  display_name: "盘口失衡狙击"
  description: "L2盘口失衡+托单/压单信号 → 超短线入场"
  category: "orderbook"
  filter:
    price_min: 5
    price_max: 300
    ob_imbalance: 0.3  # 买压阈值
```

### 方向 5: 题材轮动动量增强 (Theme Rotation Momentum)

**类型**: 中短线 / 题材轮动
**数据源**: `market_mainline.py` + `commodity_rotation.py` + `sector_filter.py` + 资金流
**信号逻辑**:
- 主线识别: 主线 Top5 题材 + 龙头股 (`market_mainline.py:19-25`)
- 商品轮动映射: 能源→金属→农产→黄金 (`commodity_rotation.py:1-7`)
- 资金流验证: 北向资金行业流入 + 龙虎榜题材净买入
- 主线→次主线轮动: 高潮期减仓主线, 加仓即将轮动的次主线

**现有证据**:
- `market_mainline.py:12-25`: 涨停池→题材聚合→主线排名(score 0-100)
- `market_mainline.py:50-56`: 主线分权重(涨停家数35%+最高板25%+梯队25%+二板宽度15%)
- `commodity_rotation.py:22-28`: 四大轮动阶段板块映射
- `commodity_rotation.py:62-151`: `detect_rotation_stage()` 事件→轮动阶段
- `sector_filter.py:31-93`: 概念板块搜索+成分股解析

**DeepSeek V4 优势**: 多源异构数据融合(涨停池+商品+资金流+政策), DeepSeek 的长上下文+推理适合做跨模态轮动判断

```yaml
theme_rotation:
  display_name: "题材轮动动量"
  description: "主线题材识别+商品轮动推演+资金流验证 → 轮动跟随"
  category: "rotation"
  tags: ["rotation", "momentum", "multi_source"]
```

---

## 七、优先级建议

| 优先级 | 方向 | 预期收益 | 实现成本 | 数据依赖 | 新增代码量估计 |
|--------|------|---------|---------|---------|--------------|
| **P0** | ② 情绪周期自适应 | 高 | 低 | 已有(sentiment_cycle) | ~200行 + YAML |
| **P0** | ③ 事件预期差套利 | 高 | 低 | 已有(event_catalyst_engine) | ~300行 + YAML |
| **P1** | ① 暗盘资金流跟随 | 高 | 中 | 已有(dark_flow) | ~500行 + YAML |
| **P1** | ⑤ 题材轮动动量 | 高 | 中 | 已有(3个模块) | ~600行 + YAML |
| **P2** | ④ 盘口失衡狙击 | 中 | 高 | 需 THS L2 付费 | ~400行 + YAML |

**P0 推荐理由**: 情绪周期和事件驱动策略完全基于已有模块, 只需 YAML 策略配置 + 策略引擎微调即可上线, 快速获得增量 alpha。

---

## 八、快速修复建议 (低 hanging fruit)

1. **接入 sentiment_cycle 到策略引擎**: 在 `_compute_factor_breakdown` 中增加周期因子, 冰点期降低风险偏好 (`strategy_engine.py:780`)
2. **创建 event_gap 策略 YAML**: 将 `event_catalyst_engine` 的预期差信号接入 `entry_candidates.py` 候选池
3. **回测对接真实 K 线**: 从 `KlineCollector` 获取历史数据, 跑 6 个 YAML 策略的回测
4. **因子集扩展**: 在 `factor_weights.py` 的 `CALIBRATABLE_FACTORS` 中增加动量/波动率因子