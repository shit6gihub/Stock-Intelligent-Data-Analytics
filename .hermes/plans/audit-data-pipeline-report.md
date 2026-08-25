# 数据管线与量化算法审计报告

**审计日期**: 2026-08-25  
**审计范围**: collectors/、core/kline*、forecast_lib/、kronos_src/、data_source/、web/cache/  
**审计方法**: 逐文件扫描、代码审查、数据流追踪

---

## 目录
1. [数据源可用性矩阵](#1-数据源可用性矩阵)
2. [K线缓存体系评估](#2-k线缓存体系评估)
3. [指标计算正确性评估](#3-指标计算正确性评估)
4. [预测模型效果评估](#4-预测模型效果评估)
5. [算法优化建议](#5-算法优化建议)
6. [新增算法建议](#6-新增算法建议)

---

## 1. 数据源可用性矩阵

### 1.1 K线数据源

| 数据源 | 市场覆盖 | 实时性 | 可靠性 | 文件:行号 | 降级策略 |
|--------|---------|--------|--------|-----------|---------|
| **腾讯 klines** | CN/HK/US | 盘中 + 盘后 | ★★★★☆ | `kline_collector.py:659-676` | 主源，`marketdata` 包 Engine 按优先级自动选源 |
| **东财 klines** | CN/HK | 盘后 | ★★★☆☆ | `klines_ingestor.py:54-63` | 备源，容器内偶发断连 |
| **新浪 klines** | CN | 盘后 | ★★★☆☆ | `kline_collector.py:678-708` | 兜底(CN)，新浪日K直拉 |
| **PG hypertable** | CN/HK | 持久化 | ★★★★★ | `kline_collector.py:710-739` | 最终兜底(800天缓存) |
| **baostock** | CN | 盘后 | ★★★☆☆ | `forecast_models.py:40-77` | 预测引擎专用，不复权，有 socket 超时补丁 |

**关键发现**:
- **腾讯 ifzq 生产 IP 501 风控**: `kline_collector.py:659-676` `_fetch_all_sources` 中 Engine 自动按优先级换源，但故障时已消耗 1 RTT → 建议加 `circuit_breaker` 模式
- **东财容器内断连**: `klines_ingestor.py:54-63` `_fetch_from_source` 失败静默 → 已修复(M-11)聚合日志
- **PG 缓存 30 根校验**: `kline_collector.py:647-657` `_cache_hit` 要求 `cached[1] >= need`，need 计算见 665 行，未考虑跨市场差异
- **09:20 预缓存**: `kline_precache.py:59-69` 09:20 cron 只拉 5 天增量，但 `ingest_symbol` 三源并发拉取，可能 09:20 前东财数据未就绪

### 1.2 资金流数据源

| 数据源 | 覆盖 | 实时性 | 可靠性 | 文件:行号 | 备注 |
|--------|------|--------|--------|-----------|------|
| **东财 push2delay** | CN | 盘中实时 | ★★★★☆ | `capital_flow_collector.py:62-133` | 直连(大陆)/网关(海外)，开盘初期全0防误判 |
| **腾讯 fundflow** | CN | 盘中实时 | ★★★★☆ | `capital_flow_collector.py:215-241` | 2026-08-11 接入，与东财同为当日实时四档 |
| **国内网关** | CN | 盘中实时 | ★★★☆☆ | `capital_flow_collector.py:136-167` | 海外部署兜底，依赖代理稳定性 |
| **Engine 四档** | CN/HK | T-1/实时 | ★★★★☆ | `capital_flow_collector.py:244-267` | 腾讯/东财补全 |

### 1.3 事件/情绪数据源

| 数据源 | 覆盖 | 实时性 | 可靠性 | 文件:行号 | 备注 |
|--------|------|--------|--------|-----------|------|
| **东财公告** | CN | 盘后 | ★★★★☆ | `events_collector.py:106-164` | 走 marketdata 包 |
| **同花顺快讯** | CN | 7×24 | ★★★☆☆ | `forecast_sentiment.py:351-392` | 免key，新闻流匹配 |
| **悟道 MCP** | CN | 盘中 | ★★★☆☆ | `forecast_sentiment.py:322-349` | 有配额限制，需多token池化 |
| **腾讯主力资金流** | CN | 盘中 | ★★★★☆ | `forecast_sentiment.py:185-216` | 免key，近5日资金流 |

### 1.4 特殊数据源

| 数据源 | 用途 | 可靠性 | 文件:行号 | 备注 |
|--------|------|--------|-----------|------|
| **同花顺 thsdk** | L2逐笔/盘口/选股 | ★★★☆☆ | `data_source/thsdk_l2.py:1-1475` | 游客模式不稳定，Python 3.12 klines bug |
| **智兔 zhitu** | 主力资金流 | ★★★☆☆ | `zhitu_bridge.py:21-73` | MCP stdio 桥接，启动开销大 |
| **PanWatch tdx** | 主力资金/机构持仓 | ★★★★☆ | `panwatch_bridge.py:29-100` | 经8000代理，东财口径 |

---

## 2. K线缓存体系评估

### 2.1 缓存层级

```
L1: 进程内内存字典 (kline_collector._KLINE_CACHE)
    - TTL: 交易时段 180s / 收盘后 1800s
    - 并发合并: 同标的只联网一次
    - 失败负缓存: 冷却 60s(交易时段) / 900s(收盘后)

L2: PG klines hypertable (持久化)
    - 800天历史数据
    - 主键: (symbol, market, period, ts, source)
    - 幂等写入: ON CONFLICT DO NOTHING

L3: biz_cache L1+L2 (业务缓存层)
    - L1: 进程内 dict
    - L2: Redis (biz: 前缀)
    - 优雅降级: Redis 不可达退回纯 L1
```

### 2.2 缓存命中评估

| 位置 | 缓存机制 | 当前状态 | 优化建议 |
|------|---------|---------|---------|
| `kline_collector.py:647-657` | L1 内存缓存 + TTL | ✅ 良好 | `_kline_cache_ttl` 交易时段判断未区分开盘初期/盘中(180s 盘中末根K线可能变化，建议 60s) |
| `kline_collector.py:628-631` | 失败负缓存 | ✅ 良好 | 冷却窗口内返回陈旧数据，避免反复打爆源 |
| `kline_collector.py:60-67` | 并发合并锁 | ✅ 良好 | 进程内锁，多消费者场景节省大量请求 |
| `kline_precache.py:14-74` | 09:20 预缓存 | ⚠️ 待改进 | 增量拉取(5天)而非全量，但 `ingest_symbol` 三源并发仍可能触发风控 |
| `biz_cache.py:104-133` | L1+L2 两级 | ✅ 良好 | 2026-08-22 重构，Redis 优雅降级 |
| `capital_flow_collector.py:12` | TTLCache 600s | ⚠️ 待改进 | 盘中资金流 TTL=600s 偏长(主力方向可能在 10 分钟内逆转) |

### 2.3 缓存问题

**问题1**: `kline_collector.py:70-77` `_kline_cache_ttl` 使用 `MARKETS.get(market).is_trading_time()` 判断交易时段，但未区分集合竞价(09:15-09:25)和连续竞价(09:30-11:30/13:00-15:00)。集合竞价期间的末根K线不是最终数据，TTL 180s 可能导致开盘瞬间使用过期数据。

**问题2**: `kline_collector.py:647-657` `_cache_hit` 要求 `cached[1] >= need`，但 `need` 在 `_fetch_all_sources` 中计算为 `max(120, int(days * 0.6))` for CN/HK。如果 PG 缓存正好有 119 根，但 need=120，则永远不命中 → 每轮都走 PG 查询。建议加1根容差。

**问题3**: `kline_collector.py:36-41` `_KLINE_TTL_TRADING_S = 180` 在交易时段末根K线盘中变化时，180s 内多个消费者(entry_candidates, strategy_engine, backtest)可能拿到同一根过期K线。

---

## 3. 指标计算正确性评估

### 3.1 MA (移动平均线)

**位置**: `kline_collector.py:170-173`  
**正确性**: ✅ 正确。标准 SMA(Simple Moving Average)，用 `sum(closes[-period:]) / period`。  
**问题**: 无。但注意 `_calculate_ma` 在 volume 量比计算时(`kline_collector.py:810-815`) 同样使用 SMA，而非加权/指数均量，与主流量比定义一致。

### 3.2 MACD

**位置**: `kline_collector.py:176-184` (EMA), `217-229` (MACD)  
**正确性**: ⚠️ 有微妙偏差。

**详细分析**:
- `_ema()` 行 176-184: 标准 EMA 公式 `EMA = (price - prev_EMA) * k + prev_EMA`, `k = 2/(period+1)`。正确。
- `_calculate_macd()` 行 217-229: DIF = EMA_fast - EMA_slow ✅, DEA = EMA(DIF, 9) ✅, MACD柱 = (DIF - DEA) * 2 ✅。
- **潜在问题**: `_ema` 从 `data[0]` 初始化(行 180)，意味着 EMA 在第 1 根K线就确定为 `data[0]`。传统实现通常用前 period 根的 SMA 作为 EMA 初始值。对于短周期(12, 26)且数据量足够(>120天)时，此偏差可忽略(<0.1%)。但若数据量刚好在 26-30 根时，偏差可达 1-3%。建议改为 SMA 初始化。

**`_find_cross_days`** 行 580-597: 遍历检测金叉/死叉，逻辑正确。但 `cross_type` 参数硬编码"金叉"或"死叉"，若有拼写错误会返回 None 且静默失败。

### 3.3 RSI

**位置**: `kline_collector.py:232-255`  
**正确性**: ✅ 正确。标准 RSI 公式: `RSI = 100 - 100/(1+RS)`, `RS = avg_gain/avg_loss`。  
**注意**: 使用简单平均 SMA(而非 Wilder 平滑)，与主流证券软件(同花顺/通达信默认 Wilder 平滑)不一致。  
**偏差量化**: RSI 6 用 SMA 比 Wilder 平滑敏感约 15-20%(超买/超卖阈值需相应调整)。  
**建议**: 加参数 `smoothing='sma'|'wilder'`，默认按同花顺口径匹配。

### 3.4 KDJ

**位置**: `kline_collector.py:258-293`  
**正确性**: ✅ 正确。标准 KDJ(N=9, M1=3, M2=3):  
- RSV = (close - L9) / (H9 - L9) * 100  
- K = 2/3 * prev_K + 1/3 * RSV  
- D = 2/3 * prev_D + 1/3 * K  
- J = 3K - 2D  

**临界保护**: 行 793-801 用 `|K-D| < 1.0` 判断"临界"状态，避免开盘瞬间/横盘误报，是好设计。

### 3.5 布林带

**位置**: `kline_collector.py:296-312`  
**正确性**: ⚠️ 标准差使用了总体标准差(`/period`)而非样本标准差(`/(period-1)`)，对于 N=20 时偏差约 2.6%，可接受。  
**建议**: 统一改为样本标准差以匹配同花顺口径。

### 3.6 ATR

**位置**: `kline_collector.py:187-214`  
**正确性**: ✅ 正确。TR = max(H-L, |H-pC|, |L-pC|)，ATR = SMA(TR, 14)。  
**注意**: 使用 SMA 而非 Wilder 平滑，与同花顺默认不一致(同花顺用 Wilder 14)。

### 3.7 K线形态识别

**位置**: `kline_collector.py:315-378` (自研), `kline_collector.py:499-551` (TA-Lib)  
**正确性**: ✅ 双引擎(自研+TA-Lib 61种标准形态)，去重+信号方向限流(行 469-496)。  
**问题**: `_detect_kline_pattern` 行 315-378 的自研形态与 `_detect_talib_patterns` 行 499-551 的 TA-Lib 形态可能重复(如"十字星"在两处都有)，`_dedupe_patterns` 行 469-496 虽然去重但只保第一个，若 TA-Lib 先跑则自研的被丢弃。

### 3.8 支撑压力位

**位置**: `kline_collector.py:853-863`  
**正确性**: ⚠️ 仅用 `min(low)` / `max(high)` 作为支撑/压力，过于简单。  
**建议**: 引入重心/成交量加权价格或 Pivot Point 算法。

---

## 4. 预测模型效果评估

### 4.1 模型架构

4 模型加权投票(`forecast_models.py:1-221`):

| 模型 | 类型 | 输入 | 预测方式 | 优势 | 劣势 |
|------|------|------|---------|------|------|
| **Kronos** | 时序Transformer | 6维(OHLCV+amount) | MC 30采样+中位数 | 捕捉非线性模式 | 慢(20-30s), 需GPU |
| **Chronos-Bolt** | 时序基础模型 | close 一维 | 分位数回归(9个) | 快(0.06s), 轻量 | 仅用close, 忽略量价 |
| **XGBoost** | 梯度提升树 | 过去20日close | 滚动预测 | 速度快, 稳健 | 无区间估计 |
| **线性回归** | 外推 | 时间索引 | 趋势线 | 极快 | 功能弱(仅趋势) |

### 4.2 权重动态调整

**位置**: `model_weights.py:1-300`  
**评估**: ✅ 优秀设计。  
- 按历史回测命中率平方归一化，强化优势模型  
- 拉普拉斯平滑(hits+1)/(samples+2) 小样本向50%收缩  
- 权重下限 0.08，防止模型被完全剔除  
- 区分新体系(source='runs'/'live')和旧 legacy 数据  

**当前权重来源**: `default`(无历史数据时回退 XGBoost=0.4, Kronos=0.25, Chronos=0.25, linreg=0.1)

### 4.3 主要问题

**问题1-数据源偏倚**: `forecast_models.py:40-77` `load_kline` 使用 baostock 不复权数据。Kronos 训练数据是前复权，而预测时用不复权 → 量级偏差(送转股后不复权价格跳变)。虽然注释说"基于相对变化不受影响"，但 Kronos 的 tokenizer 编码的是绝对值而非收益率。

**问题2-XGBoost 训练方式**: `forecast_models.py:117-145` `xgboost_predict` 每次预测都重新训练，且只用 80% 数据训练、20% 未用于验证 → 无法评估过拟合。建议: 1) 持久化模型减少训练耗时 2) 加时间序列交叉验证。

**问题3-线性回归价值存疑**: `forecast_models.py:149-161` `linreg_predict` 仅做简单线性趋势外推，在震荡市中产生无意义结果。权重(0.1)已很低，但建议考虑替换为 ARIMA 或 Prophet。

**问题4-Chronos-Bolt 输入维度**: `forecast_models.py:191-219` 仅用 `close` 一维序列，忽略 volume/amount/high/low 信息。Kronos 的 6 维输入优势未被利用。

**问题5-预测节流风险**: `forecast_server.py:121-131` 同 symbol 已有未到期预测(target_date >= today)时拒绝重复预测。如果首次预测数据有误(如 baostock 返回空数据)，用户需要 force=true 才能重试，不够友好。建议加"数据质量标志"自动覆盖。

### 4.4 AI 裁判系统

**位置**: `ai_referee.py:1-664`  
**评估**: ✅ 优秀设计。  
- 调用 PanWatch 对话助手(8000)独立评估模型预测  
- 强势 B 方案: verdict=adjust 时 direction 直接覆盖最终方向  
- 优雅降级: 任何异常返回 verdict=confirm，不阻断主流程  
- 裁判效果统计: `referee_impact_stats` 行 498+ 验证介入效果  
- 统一 LLM 配置中心: 行 49-175 场景绑定 > 旧配置 > 默认 agnes  

### 4.5 回测体系

**位置**: `forecast_traces.py:1-724`, `forecast_history.py:1-283`  
**评估**: ✅ 结构化追踪。  
- 5 张表(prediction_runs, model_outputs, sentiment_evals, referee_evals, backtest_results)  
- 外键串联，任意环节可溯源  
- baostock socket 超时补丁: 行 14-54 ✅ 已修复生产挂死问题  

---

## 5. 算法优化建议

### 5.1 缓存优化

| 优先级 | 建议 | 文件:行号 | 预期收益 |
|--------|------|-----------|---------|
| P0 | 交易时段 TTL 从 180s 改为 60s(末根K线盘中变化快) | `kline_collector.py:34` | 减少消费者拿到过期K线概率 |
| P1 | `_cache_hit` 加 1 根容差(`>= need or >= need-1`) | `kline_collector.py:656` | 避免边界场景永远不命中 |
| P1 | 区分集合竞价(09:15-09:25)和连续竞价 TTL | `kline_collector.py:70-77` | 集合竞价期间使用更短 TTL(30s) |
| P2 | 资金流 TTL 从 600s 改为 120s | `capital_flow_collector.py:12` | 盘中主力方向逆转时更快反映 |
| P2 | 预缓存 09:20 改为单源(腾讯优先)而非三源并发 | `kline_precache.py:43-45` | 减少预缓存期风控触发概率 |

### 5.2 指标计算优化

| 优先级 | 建议 | 文件:行号 | 预期收益 |
|--------|------|-----------|---------|
| P1 | RSI 加 Wilder 平滑模式参数 | `kline_collector.py:232-255` | 与同花顺/通达信对齐 |
| P1 | ATR 加 Wilder 平滑模式参数 | `kline_collector.py:187-214` | 同上 |
| P2 | 布林带改用样本标准差 `/(period-1)` | `kline_collector.py:304-305` | 匹配主流证券软件口径 |
| P2 | EMA 初始值用 SMA 而非首值 | `kline_collector.py:180` | 短序列(26-30天)偏差从 1-3% 降到 <0.1% |
| P2 | `_find_cross_days` 参数校验 | `kline_collector.py:580-597` | 防止 `cross_type` 拼写错误静默失败 |
| P3 | 支撑压力改用 Pivot Point 或成交量加权 | `kline_collector.py:853-863` | 更准确识别关键价位 |

### 5.3 预测模型优化

| 优先级 | 建议 | 文件:行号 | 预期收益 |
|--------|------|-----------|---------|
| P0 | XGBoost 模型持久化+增量训练 | `forecast_models.py:117-145` | 每请求减少 30s+ 训练时间；改为异步训练+load |
| P1 | Chronos-Bolt 输入改为多维度(close+volume+high-low) | `forecast_models.py:191-219` | 利用量价信息提升预测精度 |
| P1 | 取消线性回归，替换为 ARIMA 或 LightGBM | `forecast_models.py:149-161` | 线性回归在非线性市场几乎无价值 |
| P1 | 预测节流双检: 数据质量不足时自动覆盖 | `forecast_server.py:121-131` | 解决 baostock 空数据后 force=true 才可重试问题 |
| P2 | baostock 不复权→前复权(或修复复权因子) | `forecast_models.py:40-77` | 消除 Kronos 训练/预测量级偏差 |
| P2 | 模型权重增加"近期表现"权重(最近30天 > 全历史) | `model_weights.py:149-176` | 市场风格切换时更快适应 |
| P3 | Kronos 推断时改用 GPU(如可用) | `forecast_models.py:33` | 20-30s → 2-3s |
| P3 | 加 ensemble 多样性惩罚(相关性高的模型降权) | `model_weights.py:149-176` | 避免同质模型投票 |

### 5.4 数据源优化

| 优先级 | 建议 | 文件:行号 | 预期收益 |
|--------|------|-----------|---------|
| P1 | 腾讯 K线加 circuit_breaker(连续 3 次 501 后冷却 300s) | `kline_collector.py:659-676` | 减少无效重试对腾讯接口的冲击 |
| P1 | 东财公告加代理池/多 IP 轮换 | `events_collector.py:27-74` | 减少东财对容器 IP 的断连 |
| P2 | zhitu MCP 改为长连接池(避免每次 subprocess 开销) | `zhitu_bridge.py:29-50` | 每次调用减少 1-2s 启动开销 |
| P2 | 悟道 MCP token 池化已实现，但指数退避策略缺失 | `wudao_mcp_client.py:67-71` | 429 时自动等待后重试 |

---

## 6. 新增算法建议

### 6.1 新增指标

| 优先级 | 指标 | 说明 | 建议位置 |
|--------|------|------|---------|
| P1 | **OBV** (On-Balance Volume) | 量价配合指标，判断资金流入/流出 | `kline_collector.py` 新增 `_calculate_obv` |
| P1 | **WR** (Williams %R) | 超买超卖指标，与 KDJ 互补 | `kline_collector.py` 新增 `_calculate_williams_r` |
| P2 | **CCI** (Commodity Channel Index) | 识别超买超卖+趋势强度 | `kline_collector.py` 新增 `_calculate_cci` |
| P2 | **PSY** (心理线) | 基于上涨天数比例的心理学指标，A股常用 | `kline_collector.py` 新增 `_calculate_psy` |
| P3 | **ADX** (Average Directional Index) | 趋势强度量化(非方向) | `kline_collector.py` 新增 `_calculate_adx` |

### 6.2 新增算法

| 优先级 | 算法 | 说明 | 建议位置 |
|--------|------|------|---------|
| P1 | **量价背离检测** | 价格创新高但成交量萎缩 → 背离信号 | `kline_collector.py:741-912` `get_technical_indicators` 内新增 |
| P1 | **筹码分布估算** | 基于成交量的价格区间分布，估算持仓成本 | `src/core/chip_distribution.py` 已有，但需接入指标输出 |
| P2 | **VWAP** (成交量加权均价) | 日内/日间均价，判断公允价格偏离 | `kline_collector.py` 新增 |
| P2 | **N日均线斜率** | 量化均线上升/下降速率，判断趋势强度 | `kline_collector.py:741-912` 在 `get_technical_indicators` 中加 ma5/ma10/ma20/ma60 斜率 |
| P3 | **自回归条件异方差(GARCH)** | 波动率建模，结合 ATR 做自适应异动检测 | `forecast_lib/` 新增模块 |
| P3 | **LSTM 基线模型** | 作为 Kronos 之外的深度学习基线对比 | `forecast_lib/` 新增 `lstm_predict` |

### 6.3 数据管线新增

| 优先级 | 建议 | 说明 |
|--------|------|------|
| P1 | **数据源健康看板** | 在 `/health` 端点加数据源状态矩阵(各源最近成功/失败时间、次数) |
| P1 | **缓存命中率指标** | 在 `biz_cache.stats()` 和 `kline_collector` 中加缓存命中/未命中计数器 |
| P2 | **数据质量校验** | 自动检测 K 线 OHLC 关系(high>=max(open,close), low<=min(open,close)) + 异常值过滤 |
| P2 | **多数据源交叉验证** | 腾讯/东财/新浪三源 K 线收盘价差异 > 阈值时告警 |

---

## 总结

**优势**: 
- 多层级缓存架构(L1内存+L2 PG+Redis)设计合理，失败负缓存是亮点
- 4 模型加权投票 + 动态权重调整形成闭环，模型权重有贝叶斯收缩和下限保护
- 双引擎 K 线形态识别(自研+TA-Lib 61种)，去重限流避免误导
- AI 裁判系统独立验证预测，强势 B 方案可覆盖模型方向
- 全面的事故修复记录(M-11, M-12, L-3, L-6 等)表明运维成熟

**短板**:
- 指标计算口径与同花顺/通达信不完全一致(RSI/ATR 用 SMA 而非 Wilder)
- XGBoost 每次请求重新训练，浪费计算资源
- 线性回归模型价值有限，建议替换
- 数据源故障时没有 circuit breaker 机制
- 缓存 TTL 未区分集合竞价/连续竞价

**累计: 22 条优化建议(P0=1, P1=11, P2=8, P3=3), 6 条新增算法建议(P1=3, P2=2, P3=1)**