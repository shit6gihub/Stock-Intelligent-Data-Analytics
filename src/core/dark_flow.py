"""暗盘资金计算器 v5(2026-08-11 优化版, 截图验证对齐同花顺结构)。

数据基础(腾讯接口, 全部实测):
- 逐笔 appn=detail: 全天全量成交, 每笔 B=主动买/S=主动卖/M=中性
- dadan 档10 = 网页"大单数据"页(大单口径: 成交额/量阈值)
- 分价表 appn=price: 价位分布(价格维度)

优化点(v4 → v5):
1. 三分类: M 中性盘不再忽略(统计但不算净额)
2. 大单/暗盘分层: 大单(≥100万或≥1000手)=明盘, 中小单=暗盘(拆单藏身处)
   验证: 大单净买+4,843手 vs 中小单净买+32,460手 → 暗盘流入结构
3. 分价表价格维度: 低价承接比(价格<VWAP的买量占比) → 吸筹价位
4. 时段分解: 早盘/午盘/午后/尾盘
5. 信号: 暗盘显著流入+明盘流出 = 拆单吸筹(同花顺核心信号)
"""
from __future__ import annotations

import logging
import re
import urllib.request

from marketdata.symbol import Symbol
from marketdata.vendors.tencent_panel import fetch_price_distribution

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}

# 大单阈值(元): 网页"大单数据"页筛选口径(成交额≥100万 或 量≥1000手)
BIG_AMOUNT = 100e4   # 100万元
BIG_VOLUME = 1000    # 1000手

# 暗盘数据源(2026-08-11 预留): 环境变量 PANWATCH_DARK_SOURCE 可切换
#   tencent_ticks = 腾讯逐笔(免费, 默认, 已与同花顺暗盘验证误差7%)
#   l2_tencent    = 腾讯L2(预留, 需付费账号)
#   l2_sina       = 新浪L2(预留, 需购买)
#   l2_itick      = iTick L2(预留, 99 USDT/月)
# 未来接入付费L2时, 在 _fetch_all_ticks 处按 source 分发即可。
DARK_SOURCE = __import__("os").environ.get("PANWATCH_DARK_SOURCE", "tencent_ticks")


def _tencent_code(symbol: Symbol) -> str | None:
    code = (symbol.code or "").strip()
    if not code.isdigit() or len(code) != 6:
        return None
    if code[0] in ("6", "9") or code.startswith("688"):
        return f"sh{code}"
    if code[0] in ("0", "2", "3"):
        return f"sz{code}"
    return None


# 逐笔缓存: {code -> (ts, ticks, last_page, last_seq, day)}。盘中 TTL 30s, 避免每轮监控重复翻页。
# day = 交易日(YYYY-MM-DD), 2026-08-13 修复: 腾讯逐笔页码按天重置, 缓存跨日残留时
# 增量续拉会从昨天的 last_page 往后拉 → 拉不到今天 0 页起的数据, 返回昨天残留(实测: 2 条)。
# 跨日(day != 今天)强制全量重拉; 旧 4 元组缓存(无 day)视为无效同样重拉。
# 2026-08-12 增量续拉: 缓存记录"拉到的页码+末条序号", 过期后只从上次页码往后拉新增页
# (9:35 拉 19 页 → 9:50 只拉 19 页之后), 序号断裂(盘中数据重置)才全量重拉。
_TICKS_CACHE: dict[str, tuple[float, list[dict], int, int, str]] = {}
_TICKS_TTL = 30.0


def _cache_day() -> str:
    """当前交易日字符串(本地日期; 腾讯逐笔页码按自然日重置, 与本地日对齐足够)。"""
    import datetime
    return datetime.date.today().isoformat()


def _cache_put(code: str, now: float, ticks: list[dict], last_page: int, last_seq: int) -> None:
    """统一写缓存(带交易日)。"""
    _TICKS_CACHE[code] = (now, ticks, last_page, last_seq, _cache_day())


def _cache_stale(code: str, cached: tuple) -> bool:
    """缓存是否跨日/旧格式(4 元组无 day) → 需要全量重拉。"""
    if len(cached) < 5:
        return True
    return cached[4] != _cache_day()

# 2026-08-12 磁盘持久化: 逐笔快照落盘(/app/data/cache), 重启后同交易日
# 直接从 last_page 增量续拉, 免全量翻页(冷启动 3.7s → ~0.5s)。
_TICKS_DISK = None
_TICKS_DISK_FLUSH_TTL = 60.0  # 写盘节流: 缓存 30s 一更新, 每 60s 刷一次即可


def _ticks_persist(load_only: bool = False):
    """逐笔快照写盘(惰性初始化 + 节流由 DiskCache 控制)。

    load_only=True 时只从磁盘加载到内存(模块 import 时用), 不写盘。
    """
    global _TICKS_DISK
    try:
        if _TICKS_DISK is None:
            from src.core.disk_cache import DiskCache, register
            _TICKS_DISK = DiskCache("dark_flow_ticks", ttl=86400.0, flush_interval=_TICKS_DISK_FLUSH_TTL)
            snap = _TICKS_DISK.get("all")
            if isinstance(snap, dict) and snap:
                _TICKS_CACHE.update(snap)
            register(_TICKS_DISK)
        if not load_only:
            _TICKS_DISK.set("all", dict(_TICKS_CACHE))
    except Exception:
        pass


# 2026-08-12: 模块 import 时加载磁盘缓存(重启后首次调用前就绪), 不写盘
_ticks_persist(load_only=True)


def _fetch_all_ticks(code: str, max_pages: int = 200) -> list[dict]:
    """翻页拉取全天全量逐笔(增量续拉), 返回 [{direction, amount, volume, time}]。

    2026-08-11 打磨: 加 30s 缓存(盘中每轮监控复用) + 单页重试(腾讯偶发限流)。
    2026-08-12 增量续拉(用户设计): 缓存记录 last_page+last_seq, 过期后只拉
      新增页合并; 并发拉页(全天 19 页串行 2.9s → 并发 ~0.9s); 空页判定 ['']。
    数据源: 默认 tencent_ticks; 未来 L2 接入在此分发(PANWATCH_DARK_SOURCE)。
    """
    import time as _time
    now = _time.time()
    cached = _TICKS_CACHE.get(code)

    # 2026-08-12: TTL 内直接返回(盘中 30s 窗口零请求; 重构时勿丢此分支)
    if cached and now - cached[0] < _TICKS_TTL:
        return cached[1]

    # 2026-08-13 跨日修复: 缓存属于昨天(或旧 4 元格式) → 丢弃走全量重拉,
    # 否则增量续拉从昨天 last_page 往后拉, 拉不到今天 0 页起的数据(实测返回 2 条残留)。
    if cached and _cache_stale(code, cached):
        _TICKS_CACHE.pop(code, None)
        cached = None

    # L2 数据源分发(预留): 接入后返回 {d, amt, vol, price, t} 同构列表即可无缝替换
    if DARK_SOURCE != "tencent_ticks":
        try:
            from src.core.dark_l2 import fetch_l2_ticks  # 预留模块, 接入L2时实现
            ticks = fetch_l2_ticks(code, DARK_SOURCE)
            _cache_put(code, now, ticks, 0, 0)
            _ticks_persist()  # 2026-08-12: 快照落盘
            return ticks
        except Exception:
            pass  # L2 未接入/异常, 回退腾讯逐笔

    def _fetch_page(p: int) -> tuple[int, list[dict]]:
        """拉单页, 返回 (页码, ticks)。失败/空页返回 (p, [])。"""
        url = f"https://stock.gtimg.cn/data/index.php?appn=detail&action=data&c={code}&p={p}"
        body = None
        # 单页重试(最多2次): 腾讯偶发限流/超时
        for attempt in range(2):
            try:
                req = urllib.request.Request(url, headers=_HEADERS)
                with urllib.request.urlopen(req, timeout=8) as resp:
                    body = resp.read().decode("gbk", "replace")
                break
            except Exception:
                if attempt == 1:
                    break
        if body is None:
            return p, []
        m = re.search(r'\[(\d+),"(.*?)"\]', body)
        if not m:
            return p, []
        rows = m.group(2).split("|")
        # 空页判定(2026-08-12 修复): 只有 1 行(如竞价首笔 09:25)是正常数据页,
        # 不能按 len<2 当空页丢弃! 真正的空页是 [''](腾讯翻页到尾返回空字符串)。
        if not rows or (len(rows) == 1 and len(rows[0].strip()) == 0):
            return p, []
        out: list[dict] = []
        for r in rows:
            parts = r.split("/")
            if len(parts) < 7:
                continue
            try:
                amt = float(parts[5])
                vol = float(parts[4])
                price = float(parts[2])
                direction = parts[6]
                t = parts[1]
                seq = int(parts[0]) if parts[0].isdigit() else -1
            except (ValueError, IndexError):
                continue
            if amt > 0:
                out.append({"d": direction, "amt": amt, "vol": vol, "price": price, "t": t, "_seq": seq})
        return p, out

    def _drain_pages(start: int, max_pages: int, ticks: list[dict], batch: int = 10) -> tuple[list[dict], int, int]:
        """分批并发拉页: 每批 batch 页, 连续2空页即停。避免一次性提交 200 个 future
        (ThreadPool with 退出会等待未运行任务, 空页判定后仍慢)。"""
        import concurrent.futures as _cf
        last_page = -1
        last_seq = -1
        empty_run = 0
        p = start
        while p < max_pages:
            hi = min(p + batch, max_pages)
            with _cf.ThreadPoolExecutor(max_workers=5) as ex:
                futs = {ex.submit(_fetch_page, i): i for i in range(p, hi)}
                for fut in _cf.as_completed(futs):
                    pg, page_ticks = fut.result()
                    if page_ticks:
                        ticks.extend(page_ticks)
                        last_page = max(last_page, pg)
                        last_seq = max(last_seq, max((t.get("_seq", -1) for t in page_ticks), default=-1))
                        empty_run = 0
                    else:
                        empty_run += 1
                    if empty_run >= 2:
                        return ticks, last_page, last_seq
            p = hi
        return ticks, last_page, last_seq

    # ---- 增量续拉: 缓存存在且过期(>30s) ----
    if cached:
        _, old_ticks, old_last_page, old_last_seq = cached[0], cached[1], cached[2], cached[3]
        # 从上次页码开始拉(最后一页盘中可能未满, 重新拉完整版) + 后续新页
        start = old_last_page
        if start >= max_pages:
            # 已拉满上限, 刷新缓存时间即可
            _cache_put(code, now, old_ticks, old_last_page, old_last_seq)
            _ticks_persist()  # 2026-08-12: 快照落盘
            return old_ticks
        new_ticks: list[dict] = []
        _new_ticks, last_page, last_seq = _drain_pages(start, max_pages, new_ticks)
        if _new_ticks:
            new_first_seq = min(t.get("_seq", -1) for t in _new_ticks)
            if new_first_seq <= 0 or old_last_seq <= 0 or new_first_seq >= old_last_seq:
                # 序号连续/推进(新 seq ≥ 旧末条 seq) → 合并去重(旧最后一页可能被新完整版覆盖)
                merged = old_ticks + _new_ticks
                # 按 seq 去重: 同 seq 保留新数据(后出现的覆盖前面的)
                dedup: dict[int, dict] = {}
                for t in merged:
                    dedup[t.get("_seq", 0)] = t
                merged = sorted(dedup.values(), key=lambda t: t.get("_seq", 0))
                for t in merged:
                    t.pop("_seq", None)
                _cache_put(code, now, merged, last_page, last_seq)
                _ticks_persist()  # 2026-08-12: 快照落盘
                return merged
            # 序号断裂(新交易日/数据重置) → 全量重拉
            _TICKS_CACHE.pop(code, None)
        else:
            # 无新增(可能盘前/刚开盘): 刷新缓存时间, 保留旧数据
            _cache_put(code, now, old_ticks, old_last_page, old_last_seq)
            _ticks_persist()  # 2026-08-12: 快照落盘
            return old_ticks

    # ---- 全量拉取(无缓存 / 序号断裂后) ----
    # 阶段1: 串行探前 6 页(确定页数 + 拿首批数据; 盘中数据少时 6 页内就到尾)
    probe_pages = 6
    ticks: list[dict] = []
    last_full = -1
    consecutive_empty = 0
    for p in range(min(probe_pages, max_pages)):
        _, page_ticks = _fetch_page(p)
        if page_ticks:
            ticks.extend(page_ticks)
            last_full = p
            consecutive_empty = 0
        else:
            consecutive_empty += 1
            if consecutive_empty >= 2:
                _cache_put(code, now, ticks, last_full, max((t.get("_seq", -1) for t in ticks), default=-1))
                _ticks_persist()  # 2026-08-12: 快照落盘
                return ticks

    # 阶段2: 并发拉剩余页(每页70条, 全天通常 10-200 页)
    if last_full >= 0 and consecutive_empty < 2:
        ticks, last_page, last_seq = _drain_pages(probe_pages, max_pages, ticks)
        ticks.sort(key=lambda t: t.get("_seq", 0))
        for t in ticks:
            t.pop("_seq", None)
        _cache_put(code, now, ticks, last_page, last_seq)
        _ticks_persist()  # 2026-08-12: 快照落盘
        return ticks

    for t in ticks:
        t.pop("_seq", None)
    _cache_put(code, now, ticks, last_full, max((t.get("_seq", -1) for t in ticks), default=-1))
    _ticks_persist()  # 2026-08-12: 快照落盘
    return ticks


def _detect_split_orders(ticks: list[dict], window_sec: int = 90, min_consec: int = 3,
                         lo: float = 5e4, hi: float = 100e4,
                         prev_close: float | None = None) -> dict:
    """拆单识别 v3: 识别主力伪装的中小单(含价格背景+套牢位置, 2026-08-11 二次修正)。

    修正史:
    v1 只按"同向+时间+金额" → 把散户割肉(跌中连续卖)误判为主力拆单卖
    v2 加价格方向(逆势=跌中买/涨中卖) → 仍误判: 涨中卖可能是散户解套盘!
       (用户洞察: 震荡后散户在上涨时卖套牢筹码, 神剑实测唯一涨中卖118万全是解套盘)
    v3 加"相对昨收位置": 
      - 涨中卖 + 价格<昨收(套牢区) = 散户解套(不是主力!)
      - 涨中卖 + 价格>昨收(获利区) = 疑似主力派发
      - 跌中买 + 价格<昨收 = 主力抄底吸筹(强信号)
      - 跌中买 + 价格>昨收 = 回落承接(中性)

    Returns: {buy_amt, sell_amt, net, contrarian_net, herd_buy, herd_sell, groups}
    """
    def _t2s(t: str) -> int:
        h, m, s = t.split(":")
        return int(h) * 3600 + int(m) * 60 + int(s)

    suspect_buy = suspect_sell = 0.0   # 疑似主力(逆势+位置确认)
    herd_buy = herd_sell = 0.0         # 散户(顺势/解套)
    groups = []
    seq = []
    for tk in ticks:
        if lo <= tk["amt"] <= hi:
            seq.append(tk)
        else:
            if len(seq) >= min_consec and all(x["d"] == seq[0]["d"] for x in seq):
                dur = _t2s(seq[-1]["t"]) - _t2s(seq[0]["t"])
                if dur <= window_sec:
                    g = _classify_split(seq, prev_close)
                    groups.append(g)
                    if g["contrarian"]:
                        if g["d"] == "B":
                            suspect_buy += g["amt"]
                        else:
                            suspect_sell += g["amt"]
                    else:
                        if g["d"] == "B":
                            herd_buy += g["amt"]
                        else:
                            herd_sell += g["amt"]
            seq = []
    # 尾组
    if len(seq) >= min_consec and all(x["d"] == seq[0]["d"] for x in seq):
        dur = _t2s(seq[-1]["t"]) - _t2s(seq[0]["t"])
        if dur <= window_sec:
            g = _classify_split(seq, prev_close)
            groups.append(g)
            if g["contrarian"]:
                if g["d"] == "B":
                    suspect_buy += g["amt"]
                else:
                    suspect_sell += g["amt"]
            else:
                if g["d"] == "B":
                    herd_buy += g["amt"]
                else:
                    herd_sell += g["amt"]

    groups.sort(key=lambda g: -g["amt"])
    return {
        "buy_amt": round(suspect_buy),
        "sell_amt": round(suspect_sell),
        "net": round(suspect_buy - suspect_sell),
        "herd_buy": round(herd_buy),
        "herd_sell": round(herd_sell),
        "groups": groups[:10],
    }


def _classify_split(seq: list[dict], prev_close: float | None) -> dict:
    """单组拆单分类, 返回组信息 + contrarian 标记。"""
    direction = seq[0]["d"]
    amt_sum = sum(x["amt"] for x in seq)
    p0, p1 = seq[0]["price"], seq[-1]["price"]
    price_dir = "up" if p1 > p0 else ("down" if p1 < p0 else "flat")
    # 相对昨收位置(套牢区 vs 获利区)
    below_prev = prev_close is not None and p0 < prev_close
    contrarian = False
    reason = ""
    if direction == "B" and price_dir == "down" and below_prev:
        contrarian = True      # 跌中买+套牢区 = 主力抄底吸筹(强信号)
        reason = "主力抄底"
    elif direction == "S" and price_dir == "up" and not below_prev:
        contrarian = True      # 涨中卖+获利区 = 主力派发
        reason = "主力派发"
    elif direction == "S" and price_dir == "up" and below_prev:
        contrarian = False     # 涨中卖+套牢区 = 散户解套盘(用户洞察!)
        reason = "散户解套"
    elif direction == "B" and price_dir == "down" and not below_prev:
        contrarian = False     # 跌中买+获利区 = 回落承接(中性)
        reason = "回落承接"
    else:
        reason = "横盘"

    return {
        "d": direction, "n": len(seq), "amt": round(amt_sum),
        "t0": seq[0]["t"], "t1": seq[-1]["t"],
        "p0": p0, "p1": p1,
        "contrarian": contrarian, "price_dir": price_dir, "reason": reason,
    }


# ── 内盘外盘口诀(2026-08-13)──────────────────────────────────────────────
# 7 条实战口诀(规则预判, 只提示不改结论)。数据基础: 腾讯 Quote 的
# volume_outer(外盘/主动买) + volume_inner(内盘/主动卖) + change_pct + volume_ratio。
_MNEMONIC_STRONG = 55.0       # ①~④ 单边占比阈值(外盘或内盘 >55%)
_MNEMONIC_BALANCE = 10.0      # ⑤ 内外盘相当: |买%-卖%| < 10%
_MNEMONIC_DOUBLE_LOW = 30.0   # ⑥ 双小: 主动买+主动卖 < 30% 总成交量
_MNEMONIC_DOUBLE_HIGH = 85.0  # ⑦ 双大: 主动买+主动卖 > 85% 总成交量
_MNEMONIC_VOL_RATIO = 1.5     # ① ② 放量: 量比 > 1.5
_MNEMONIC_MOVE = 0.5          # 有效涨跌: |涨跌%| > 0.5 才算涨/跌(剔除噪音)
_MNEMONIC_FLAT = 1.0          # ⑦ 价格不动: |涨跌%| <= 1.0
_MNEMONIC_OSCILLATE = 3.0     # ⑥ 震荡: 0.5% < |涨跌%| <= 3%(有波动无单边)


def _num(v) -> float | None:
    """安全转 float, 失败/None 返回 None。"""
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _position_from_range(quote: dict | None) -> str:
    """今日振幅区间内估算位置(无K线时的简化口径): 分位 >=0.66 high / <=0.33 low / 其他 mid。"""
    try:
        if not quote:
            return "unknown"
        hi = _num(quote.get("high_price"))
        lo = _num(quote.get("low_price"))
        price = _num(quote.get("current_price"))
        if hi and lo and price and hi > lo:
            pct = (price - lo) / (hi - lo)
            if pct >= 0.66:
                return "high"
            if pct <= 0.33:
                return "low"
            return "mid"
    except Exception:
        pass
    return "unknown"


def _estimate_position(symbol: Symbol, quote: dict | None = None) -> str:
    """现价位置估算: 20日K线分位优先, 失败回退今日振幅区间, 再失败 unknown。"""
    try:
        from marketdata.vendors.kline import fetch_tencent_kline_raw
        code = _tencent_code(symbol)
        if code:
            bars = fetch_tencent_kline_raw(code, 20)
            if bars and len(bars) >= 5:
                highs = [b.high for b in bars if b.high]
                lows = [b.low for b in bars if b.low]
                if highs and lows:
                    hi, lo = max(highs), min(lows)
                    price = _num(bars[-1].close) or _num((quote or {}).get("current_price"))
                    if hi > lo and price:
                        pct = (price - lo) / (hi - lo)
                        if pct >= 0.66:
                            return "high"
                        if pct <= 0.33:
                            return "low"
                        return "mid"
    except Exception:
        pass
    return _position_from_range(quote)


def _signal_direction(signal: str) -> int:
    """从主力意图 signal 文本粗判方向: +1 偏多 / -1 偏空 / 0 中性。

    用于口诀 divergence(背离)判定: 信号文本同时含多空词(如"净流出…疑洗盘吸筹")
    视为中性, 不判背离, 避免把模糊信号误报成背离。
    """
    s = signal or ""
    bull = ("净流入" in s) or ("吸筹" in s)
    bear = ("净流出" in s) or ("派发" in s) or ("出货" in s) or ("抛压" in s)
    if bull and not bear:
        return 1
    if bear and not bull:
        return -1
    return 0


def _judge_mnemonic(dark: dict, quote: dict | None = None) -> dict | None:
    """内盘外盘 7 口诀判定(规则预判, 只提示不改结论)。

    输入: compute_dark_flow 结果 dark + 腾讯 Quote 字段 dict(volume_outer/
    volume_inner/change_pct/volume_ratio/high_price/low_price/current_price/volume)。
    buy_pct = 外盘/(外盘+内盘)*100, sell_pct 反向; 涨跌/放量/位置按阈值判定。

    命中返回 {mnemonic: 口诀名, direction: 看涨/看跌/观望/警惕/关注/中性,
    divergence: bool(口诀方向与主力意图 signal 方向是否背离), detail: 解析文本};
    无命中返回 None。

    判定优先级(避免多口诀同时命中时自相矛盾):
      ⑥ 双小(结构)→ ⑤ 平衡(良性解读优先, 否则每个横盘日都被 ⑦ 误报对倒)→ ⑦ 双大
      → ① ② 放量确认的强信号 → ③ ④ 位置信号
    注意: 腾讯口径 volume≈外盘+内盘(active_ratio≈100%), 故 ⑥ 双小在当前数据源
    几乎不触发(保留规则, 未来 L2 含中性盘时生效); ⑦ 仅在单边失衡+价格不动时触发。
    """
    if not dark or not quote:
        return None
    outer = _num(quote.get("volume_outer"))
    inner = _num(quote.get("volume_inner"))
    change_pct = _num(quote.get("change_pct"))
    volume_ratio = _num(quote.get("volume_ratio"))
    if outer is None or inner is None or (outer + inner) <= 0 or change_pct is None:
        return None

    total = outer + inner
    buy_pct = outer / total * 100
    sell_pct = inner / total * 100

    # 位置: 优先 compute_dark_flow 的 inner_outer.position(20日分位), 缺失回退今日振幅
    position = ((dark.get("inner_outer") or {}).get("position")) or _position_from_range(quote)

    # 成交量口径: 主动盘(外+内)占总成交量比例(腾讯 volume 总手)
    quote_vol = _num(quote.get("volume"))
    active_ratio = total / quote_vol * 100 if (quote_vol and quote_vol > 0) else 100.0

    # 涨跌/放量/形态
    up = change_pct > _MNEMONIC_MOVE
    down = change_pct < -_MNEMONIC_MOVE
    flat = abs(change_pct) <= _MNEMONIC_MOVE
    no_move = abs(change_pct) <= _MNEMONIC_FLAT       # ⑦ 价格不动
    oscillate = _MNEMONIC_MOVE < abs(change_pct) <= _MNEMONIC_OSCILLATE  # ⑥ 震荡
    volume_up = volume_ratio is not None and volume_ratio > _MNEMONIC_VOL_RATIO  # 放量
    double_small = active_ratio < _MNEMONIC_DOUBLE_LOW   # ⑥ 双小
    double_big = active_ratio > _MNEMONIC_DOUBLE_HIGH     # ⑦ 双大
    balance = abs(buy_pct - sell_pct) < _MNEMONIC_BALANCE  # ⑤ 内外盘相当

    head = (
        f"外盘{buy_pct:.1f}%/内盘{sell_pct:.1f}%, 涨跌{change_pct:+.2f}%, "
        f"量比{volume_ratio:.2f}, 位置:{position}, 主动盘占比{active_ratio:.0f}%"
    )

    # (条件, 口诀名, 方向, 解析文本) —— 按优先级排列
    rules = [
        # ⑥ 内外盘双小(主动盘<30%成交)+震荡 → 控盘洗盘(关注)
        (double_small and oscillate, "控盘洗盘", "关注",
         "主动买卖盘占比<30%却仍在震荡 → 交投清淡, 疑似主力高度控盘后的洗盘(关注)"),
        # ⑤ 内外盘相当(|买-卖|<10%)+横盘 → 多空平衡(观望)
        (balance and flat, "多空平衡", "观望",
         "内外盘占比接近且横盘 → 多空力量平衡, 方向不明(观望)"),
        # ⑦ 内外盘双大(主动盘>85%)+价格不动 → 对倒造假(警惕)
        (double_big and no_move, "对倒造假", "警惕",
         "主动买卖盘占比>85%但价格几乎不动, 且内外盘单边失衡 → 疑似对倒制造成交量, 警惕出货陷阱(警惕)"),
        # ① 外盘大(>55%)+涨+放量 → 真金进攻(看涨)
        (buy_pct > _MNEMONIC_STRONG and up and volume_up, "真金进攻", "看涨",
         "外盘占比高+上涨+放量 → 主动性买盘真金进攻(看涨)"),
        # ② 内盘大(>55%)+跌+放量 → 主力撤退(看跌)
        (sell_pct > _MNEMONIC_STRONG and down and volume_up, "主力撤退", "看跌",
         "内盘占比高+下跌+放量 → 主动性卖盘汹涌, 疑似主力撤退(看跌)"),
        # ③ 外盘大+跌+高位 → 诱多出货(警惕)
        (buy_pct > _MNEMONIC_STRONG and down and position == "high", "诱多出货", "警惕",
         "外盘占比高却下跌且处高位 → 疑似边拉边出诱多, 警惕高位派发(警惕)"),
        # ④ 内盘大+涨+低位 → 压盘吸筹(看涨)
        (sell_pct > _MNEMONIC_STRONG and up and position == "low", "压盘吸筹", "看涨",
         "内盘占比高但上涨且处低位 → 疑似压盘吸筹, 低位换手收集筹码(看涨)"),
        # ⑤ 内外盘相当(|买-卖|<10%)+横盘 → 多空平衡(观望)
        (balance and flat, "多空平衡", "观望",
         "内外盘占比接近且横盘 → 多空力量平衡, 方向不明(观望)"),
    ]
    for cond, name, direction, text in rules:
        if not cond:
            continue
        # 背离: 口诀方向与主力意图 signal 方向相反(仅数据充分且信号方向明确时判定)
        divergence = False
        if dark.get("data_status") == "ok":
            sig_dir = _signal_direction(dark.get("signal", ""))
            mn_dir = {"看涨": 1, "看跌": -1}.get(direction, 0)
            divergence = sig_dir != 0 and mn_dir != 0 and sig_dir != mn_dir
        if divergence:
            text += " ⚠️ 与主力资金意图方向背离, 优先以主力意图为准!"
        return {
            "mnemonic": name,
            "direction": direction,
            "divergence": divergence,
            "detail": f"{head} → {text}",
        }
    return None


def compute_dark_flow(symbol: Symbol) -> dict | None:
    """计算暗盘资金 v5: 三分类 + 大单/暗盘分层 + 价格维度 + 时段。"""
    code = _tencent_code(symbol)
    if not code:
        return None
    ticks = _fetch_all_ticks(code)
    if not ticks:
        return None

    # 昨收(用于套牢区判断: 涨中卖+低于昨收=散户解套, 非主力派发)
    prev_close = None
    quote_dict = None
    try:
        from marketdata.vendors.tencent import TencentQuoteVendor
        q = TencentQuoteVendor().fetch([symbol], {})[0]
        prev_close = q.prev_close if q.prev_close else None
        quote_dict = {
            "current_price": q.current_price,
            "high_price": q.high_price,
            "low_price": q.low_price,
            "change_pct": q.change_pct,
            "volume_ratio": q.volume_ratio,
            "volume_outer": q.volume_outer,
            "volume_inner": q.volume_inner,
        }
    except Exception:
        pass

    # ---- 基础统计(三分类, 竞价单单独处理) ----
    # 关键(2026-08-11 三表破解): 9:25-9:30 集合竞价撮合不是"主动买入",
    # 腾讯网页大单把它算中性盘。方向标记 B 在竞价时段不可信。
    buy_amt = sell_amt = m_amt = 0.0
    buy_vol = sell_vol = m_vol = 0.0
    auction_amt = auction_vol = 0.0   # 竞价单(集合竞价撮合)
    # 大单分层
    big_buy_amt = big_sell_amt = 0.0
    small_buy_amt = small_sell_amt = 0.0
    # 时段
    seg = {"morning": 0.0, "mid": 0.0, "afternoon": 0.0, "tail": 0.0}

    for tk in ticks:
        d, amt, vol, t = tk["d"], tk["amt"], tk["vol"], tk["t"]
        is_big = amt >= BIG_AMOUNT or vol >= BIG_VOLUME
        hm = t[:5]
        # 竞价时段(9:25-9:30): 集合竞价撮合, 方向不可信, 单独统计
        if t < "09:30":
            auction_amt += amt
            auction_vol += vol
            continue
        if d == "B":
            buy_amt += amt; buy_vol += vol
            if is_big: big_buy_amt += amt
            else: small_buy_amt += amt
            sign = 1.0
        elif d == "S":
            sell_amt += amt; sell_vol += vol
            if is_big: big_sell_amt += amt
            else: small_sell_amt += amt
            sign = -1.0
        else:  # M 中性
            m_amt += amt; m_vol += vol
            continue
        # 时段净额
        if hm < "10:30":
            seg["morning"] += sign * amt
        elif hm < "11:30":
            seg["mid"] += sign * amt
        elif hm < "14:30":
            seg["afternoon"] += sign * amt
        else:
            seg["tail"] += sign * amt

    # ---- 结果 ----
    dark_net = buy_amt - sell_amt          # 全量主动净额(剔除竞价)
    big_net = big_buy_amt - big_sell_amt   # 超大单(≥100万)净额
    small_net = small_buy_amt - small_sell_amt  # 中小单(<100万)净额

    # 当日主力意图(2026-08-11 修正, 腾讯官方口径):
    # 主力 = 成交金额≥20万 或 股数≥6万股(600手); 超大单≥100万; 大单=主力-超大单
    # ⚠️ 必须剔除竞价单(9:25-9:30 撮合非主动买卖), 否则主力净额被竞价B污染
    non_auction = [t for t in ticks if t["t"] >= "09:30"]
    main_buy_amt = sum(t["amt"] for t in non_auction if (t["amt"] >= 20e4 or t["vol"] >= 600) and t["d"] == "B")
    main_sell_amt = sum(t["amt"] for t in non_auction if (t["amt"] >= 20e4 or t["vol"] >= 600) and t["d"] == "S")
    main_net = main_buy_amt - main_sell_amt           # 主力净额(≥20万, 剔除竞价)
    big_net = big_buy_amt - big_sell_amt               # 超大单净额(≥100万, 已剔除竞价)
    mid_net = main_net - big_net                        # 大单净额(20万-100万)
    retail_buy_amt = sum(t["amt"] for t in non_auction if not (t["amt"] >= 20e4 or t["vol"] >= 600) and t["d"] == "B")
    retail_sell_amt = sum(t["amt"] for t in non_auction if not (t["amt"] >= 20e4 or t["vol"] >= 600) and t["d"] == "S")
    retail_net = retail_buy_amt - retail_sell_amt      # 散户净额(<20万, 剔除竞价)
    main_intensity = (main_buy_amt + main_sell_amt) / (buy_amt + sell_amt) * 100 if (buy_amt + sell_amt) else None  # 主力参与度%
    main_buy_ratio = main_buy_amt / (main_buy_amt + main_sell_amt) * 100 if (main_buy_amt + main_sell_amt) else None  # 主力买占主力成交%

    result = {
        "dark_net": round(dark_net),           # 全量主动净额
        "main_net": round(main_net),           # 主力净额(≥20万, 腾讯官方口径)
        "big_net": round(big_net),             # 超大单净额(≥100万)
        "mid_net": round(mid_net),             # 大单净额(20-100万)
        "small_net": round(retail_net),        # 散户净额(<20万)
        "buy_amt": round(buy_amt), "sell_amt": round(sell_amt),
        "m_amt": round(m_amt),
        "buy_vol": round(buy_vol), "sell_vol": round(sell_vol), "m_vol": round(m_vol),
        "buy_pct": round(buy_vol / (buy_vol + sell_vol + m_vol) * 100, 1) if (buy_vol + sell_vol + m_vol) else None,
        "sell_pct": round(sell_vol / (buy_vol + sell_vol + m_vol) * 100, 1) if (buy_vol + sell_vol + m_vol) else None,
        "auction_amt": round(auction_amt),   # 竞价撮合金额(元)
        "auction_vol": round(auction_vol),   # 竞价撮合手数
        "main_intensity": round(main_intensity, 1) if main_intensity is not None else None,  # 主力参与度%
        "main_buy_ratio": round(main_buy_ratio, 1) if main_buy_ratio is not None else None,  # 主力买占比%
        # 2026-08-13: 内盘外盘结构化字段(分时卡片用)。buy_amt/sell_amt = 主动买/卖金额(元),
        # buy_pct/sell_pct = 占比(基于 vol, 含中性盘分母); volume_ratio/change_pct 取自腾讯 Quote;
        # position = 现价位置(20日分位优先, 回退今日振幅, 再失败 unknown)
        "inner_outer": {
            "buy_amt": round(buy_amt),
            "sell_amt": round(sell_amt),
            "buy_pct": round(buy_vol / (buy_vol + sell_vol + m_vol) * 100, 1) if (buy_vol + sell_vol + m_vol) else None,
            "sell_pct": round(sell_vol / (buy_vol + sell_vol + m_vol) * 100, 1) if (buy_vol + sell_vol + m_vol) else None,
            "volume_ratio": (quote_dict or {}).get("volume_ratio"),
            "change_pct": (quote_dict or {}).get("change_pct"),
            "position": _estimate_position(symbol, quote_dict),
        },
        "segments": {k: round(v) for k, v in seg.items()},
        "tick_count": len(ticks),
        # 2026-08-12: 盘中数据量门槛 —— 竞价/开盘初期(非竞价成交<30笔)不算主力意图,
        # 直接给"数据不足"标记, 避免把竞价单/零星成交误判成吸筹派发
        "data_status": "insufficient" if len(non_auction) < 30 else "ok",
    }

    # ---- 价格维度(分价表) ----
    # 真实字段(2026-08-11 截图破解): 价~主动买量~总成交量~委托买量~委托卖量
    # 竞买率 = 主动买量/总成交量 → 每价位买盘强度
    try:
        from marketdata.vendors.tencent_panel import fetch_price_distribution
        prices = fetch_price_distribution(symbol, limit=70)
        if prices and len(prices) > 5:
            total_vol = sum(px["volume"] for px in prices)
            if total_vol > 0:
                vwap = sum(px["price"] * px["volume"] for px in prices) / total_vol
                result["vwap"] = round(vwap, 2)
                # 低价承接: 价格<VWAP 的成交量占比
                low_vol = sum(px["volume"] for px in prices if px["price"] < vwap)
                result["low_price_ratio"] = round(low_vol / total_vol, 3)

                # 吸筹价位: 主成交区(量>3万手)且竞买率>55% 的价位
                strong_buy_zones = []
                strong_sell_zones = []
                for px in prices:
                    vol = px.get("volume") or 0
                    buy_vol = px.get("buy_volume") or 0
                    if vol < 30000:
                        continue
                    ratio = buy_vol / vol * 100 if vol else 0
                    if ratio >= 55:
                        strong_buy_zones.append({"price": px["price"], "ratio": round(ratio, 1), "vol": round(vol)})
                    elif ratio <= 45:
                        strong_sell_zones.append({"price": px["price"], "ratio": round(ratio, 1), "vol": round(vol)})
                result["strong_buy_zones"] = strong_buy_zones[:6]   # 吸筹价位
                result["strong_sell_zones"] = strong_sell_zones[:6] # 抛压价位
    except Exception:
        pass

    result["signal"] = _judge_signal(
        dark_net, main_net, big_net, mid_net, retail_net, seg,
        result.get("low_price_ratio"),
        result.get("strong_buy_zones", []),
        result.get("strong_sell_zones", []),
        auction_amt, auction_vol,
        result.get("main_intensity"), result.get("main_buy_ratio"),
    )

    # ---- 拆单识别(主力伪装的中小单) ----
    try:
        split = _detect_split_orders(ticks, prev_close=prev_close)
        result["split_order"] = split
    except Exception as e:
        logger.debug(f"拆单识别失败: {e}")

    # ---- 价位级承接分析(2026-08-11 用户洞察) ----
    # 找"大单卖+中小买"(主力砸散户接) 或 "大单买+中小卖"(主力吸筹) 的价位
    try:
        from collections import defaultdict
        by_price = defaultdict(lambda: {"big_buy": 0.0, "big_sell": 0.0, "small_buy": 0.0, "small_sell": 0.0})
        for tk in ticks:
            p = round(tk["price"], 2)
            is_big = tk["amt"] >= BIG_AMOUNT
            if is_big:
                if tk["d"] == "B": by_price[p]["big_buy"] += tk["amt"]
                elif tk["d"] == "S": by_price[p]["big_sell"] += tk["amt"]
            else:
                if tk["d"] == "B": by_price[p]["small_buy"] += tk["amt"]
                elif tk["d"] == "S": by_price[p]["small_sell"] += tk["amt"]
        # 主力吸筹位: 大单净买>800万 且 中小单净卖(散户割)
        absorb_zones, distribute_zones = [], []
        for p, d in by_price.items():
            total = d["big_buy"] + d["big_sell"] + d["small_buy"] + d["small_sell"]
            if total < 1000e4:  # 只留 1000万以上成交的价位
                continue
            big_net = d["big_buy"] - d["big_sell"]
            small_net = d["small_buy"] - d["small_sell"]
            if big_net > 800e4 and small_net < -300e4:
                absorb_zones.append({"price": p, "big_net": round(big_net), "small_net": round(small_net)})
            elif big_net < -800e4 and small_net > 300e4:
                distribute_zones.append({"price": p, "big_net": round(big_net), "small_net": round(small_net)})
        result["absorb_zones"] = sorted(absorb_zones, key=lambda x: -x["big_net"])[:6]
        result["distribute_zones"] = sorted(distribute_zones, key=lambda x: x["big_net"])[:6]
    except Exception as e:
        logger.debug(f"承接价位分析失败: {e}")

    # ---- 5日主力阶段(2026-08-11 用户洞察: 主力不可能一直买/散户不能一直卖) ----
    try:
        from marketdata.vendors.tencent_fundflow import TencentFundflowVendor
        cf = TencentFundflowVendor().fetch([symbol], {})[0]
        today_main = cf.main_net_inflow        # 今日主力净(腾讯口径, 元)
        main_5d = cf.main_net_5d               # 近5日主力净累计(元)
        if main_5d is not None:
            today_main = today_main or 0.0
            result["today_main_5d_net"] = round(today_main)
            result["main_5d_net"] = round(main_5d)
            if main_5d > 0 and today_main < 0:
                result["phase"] = "吸筹后转派发(5日净流入但今日流出, 主力开始获利了结)"
            elif main_5d > 0 and today_main > 0:
                result["phase"] = "持续吸筹(5日+今日均净流入)"
            elif main_5d < 0 and today_main > 0:
                result["phase"] = "派发后反弹(5日净流出但今日流入, 观察是否止跌)"
            elif main_5d < 0 and today_main < 0:
                result["phase"] = "持续派发(5日+今日均净流出)"
            else:
                result["phase"] = "阶段不明(数据不足)"
    except Exception as e:
        logger.debug(f"5日阶段判断失败: {e}")

    result["note"] = "v11: 拆单+套牢位+5日主力阶段(双向)"
    return result


def _judge_signal(dark_net: float, main_net: float, big_net: float, mid_net: float,
                  retail_net: float, seg: dict, low_ratio: float | None,
                  strong_buy: list | None = None, strong_sell: list | None = None,
                  auction_amt: float = 0.0, auction_vol: float = 0.0,
                  main_intensity: float | None = None, main_buy_ratio: float | None = None) -> str:
    """信号判定 v14: 主力买入强度(吸筹力度) + 净额方向。

    2026-08-11 二次修正(用户洞察: 同花顺暗盘流入多 = 主力在吸筹):
    - 同花顺"暗盘" ≈ 主力主动买入强度(占成交额 40-80%), 不是净额
    - 神剑: 主力买8.6亿(占40.6%)净额仅-2.9% → 判吸筹(同花顺一致), 不再判"托盘出货"
    - 主力买入强度 = 主力参与度%(占全市场成交) + 主力买占比%(买占主力成交)
    """
    threshold = 500e4  # 500万
    tail = seg.get("tail", 0)
    low_boost = "低位承接" if (low_ratio or 0) > 0.4 else ""
    auction_note = f"竞价{auction_amt/1e4:.0f}万" if auction_amt > 0 else ""
    # 吸筹力度: 主力参与度>35% 且 主力买占比>48% = 强吸筹
    strong_absorb = (main_intensity or 0) >= 35 and (main_buy_ratio or 0) >= 48
    intensity_note = f"主力买占比{main_buy_ratio:.0f}%" if main_buy_ratio else ""

    # 主力净方向(≥20万)
    if main_net > threshold:
        if tail > 0:
            return f"主力净流入+尾盘加仓(吸筹){low_boost}|{auction_note}|{intensity_note}"
        return f"主力净流入(主动买占优){low_boost}|{auction_note}|{intensity_note}"
    if main_net < -threshold:
        # 净流出但主力参与度高(买入强度大) = 对倒换手/洗盘吸筹
        if strong_absorb:
            return f"主力净流出但参与度高({main_buy_ratio:.0f}%买占)疑洗盘吸筹|{auction_note}"
        if tail < 0:
            return f"主力净流出+尾盘抛压(出货)|{auction_note}"
        return f"主力净流出(主动卖占优)|{auction_note}"
    # 平衡: 看买入强度定吸筹/派发
    if strong_absorb:
        return f"主力平衡但参与度高({main_buy_ratio:.0f}%买占)疑吸筹|{auction_note}"
    return f"主力平衡(买卖接近)|{auction_note}"
