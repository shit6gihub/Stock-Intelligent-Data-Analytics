"""模型权重动态调整 — 预测质量闭环(后端)

预测引擎 4 模型加权投票(Kronos / Chronos / XGBoost / 线性回归)的权重不再固定,
改为按历史回测命中率自动调整, 形成闭环:

    预测 → 到期验证(prediction_outcome) → 回测(forecast_server._do_backtest)
        → 各模型 accuracy_pct → 权重(命中率平方, 归一化) → predict() 下次生效

数据流:
- load_weights(): 从 ~/.panwatch_forecast.db 的 backtest_results 表读取各模型历史
  命中率(model_hits_json), 按"命中率平方 → 归一化"计算权重; 无任何可用数据时
  回退固定默认 MODEL_WEIGHTS。
  * 聚合所有回测行(而非只看最新一条): backtest_results 是 per-symbol 的, 存在
    1-2 样本的噪声行(如 2026-08-12 的 1 样本回测), 只取最新一条会把噪声当真理;
    跨行聚合 samples/hits 更稳。改为"只看最新一条"只需换 _load_pooled_model_stats。
- update_weights_after_backtest(backtest_result): 每次回测算完 model_summary 后
  调用, 把最新命中率写回轻量 JSON 文件(~/.panwatch_model_weights.json), 作为
  DB 之外的兜底与审计记录(DB 侧由 save_backtest_result 负责写回, 双写保证闭环)。

设计要点:
- 命中率平方: 强化优势模型(75% vs 57% → 权重差更大), 但保留全部 4 模型参与。
- 无数据默认 0.25: 某模型没有历史数据(或样本不足)时给中性权重, 不因缺数据被剔除。
- 权重下限 0.08: 即使某模型历史命中率极低也保留最低参与度(防过度拟合/黑天鹅)。
- 样本数下限 MIN_SAMPLES: 少于该样本数的命中率视为噪声, 按"无数据"处理。
- 历史遗留模型名映射: linear_reg → linreg(老代码命名); lag_llama 已被 chronos
  替代且非同源模型, 直接忽略(chronos 在有自身回测数据前用默认权重)。
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime

# 4 个参与投票的模型(与 forecast_server.predict 的 votes 名称一一对应)
MODEL_NAMES = ["xgboost", "kronos", "chronos", "linreg"]

# 兜底默认权重(无任何历史数据时使用)
DEFAULT_MODEL_WEIGHTS = {"xgboost": 0.4, "kronos": 0.25, "chronos": 0.25, "linreg": 0.1}

# 单模型权重下限: 命中率再差也保留最低参与度, 避免某模型被完全剔除
WEIGHT_FLOOR = 0.08

# 样本数下限: 少于该样本数的 accuracy_pct 视为噪声, 按"无数据"给默认权重
MIN_SAMPLES = 10

# 历史遗留模型名 → 当前模型名(lag_llama 已被 chronos 替代, 非同源, 不映射)
LEGACY_NAME_MAP = {"linear_reg": "linreg"}

# 轻量权重落盘文件(update_weights_after_backtest 写, load_weights 兜底读)
WEIGHTS_FILE = os.path.join(os.path.expanduser("~"), ".panwatch_model_weights.json")

try:
    from forecast_paths import FORECAST_DB_PATH  # forecast_lib 在 sys.path(direct 运行)
except ImportError:  # pragma: no cover
    try:
        from forecast_lib.forecast_paths import FORECAST_DB_PATH  # /tmp/PanWatch 在 sys.path
    except ImportError:  # pragma: no cover
        FORECAST_DB_PATH = os.path.join(os.path.expanduser("~"), ".panwatch_forecast.db")

# load_weights() 最近一次判定来源: "default" | "history" | "file"(供日志)
_last_source = "default"


def last_weights_source() -> str:
    """返回最近一次 load_weights() 的权重来源(default/history/file)。"""
    return _last_source


# ---------------------------------------------------------------------------
# DB 读取
# ---------------------------------------------------------------------------

def _load_pooled_model_stats(db_path: str | None = None) -> dict:
    """从 backtest_results 表聚合所有行的 model_hits_json, 按模型合并 samples/hits。

    返回 {model_name: {"samples": int, "hits": int, "accuracy_pct": float}}。
    聚合而非只取最新一条: 最新行可能是 1-2 样本的噪声回测, 跨行合并更稳。
    """
    db_path = db_path or FORECAST_DB_PATH
    if not os.path.exists(db_path):
        return {}
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT model_hits_json FROM backtest_results ORDER BY id"
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        return {}

    pooled: dict[str, dict] = {}
    for r in rows:
        try:
            mh = json.loads(r["model_hits_json"] or "{}")
        except (ValueError, TypeError):
            continue
        if not isinstance(mh, dict):
            continue
        for raw_name, s in mh.items():
            name = LEGACY_NAME_MAP.get(raw_name, raw_name)
            if name not in MODEL_NAMES or not isinstance(s, dict):
                continue
            try:
                samples = int(s.get("samples") or 0)
                hits = int(s.get("hits") or 0)
            except (TypeError, ValueError):
                continue
            if samples <= 0:
                continue
            agg = pooled.setdefault(name, {"samples": 0, "hits": 0})
            agg["samples"] += samples
            agg["hits"] += hits
    for agg in pooled.values():
        agg["accuracy_pct"] = round(agg["hits"] / agg["samples"] * 100, 1)
    return pooled


def _has_usable_data(stats: dict) -> bool:
    """是否存在样本数达标(≥MIN_SAMPLES)的模型统计。"""
    return any(
        isinstance(s, dict) and int(s.get("samples") or 0) >= MIN_SAMPLES
        for s in stats.values()
    )


# ---------------------------------------------------------------------------
# 权重算法
# ---------------------------------------------------------------------------

def _compute_weights(stats: dict) -> dict:
    """按命中率平方 → 归一化 计算 4 模型权重。

    - 某模型无数据/样本不足: 原始权重给 0.25(中性, 不剔除)
    - 有数据: 原始权重 = (accuracy_pct/100) ** 2(强化优势)
    - 归一化 + 下限保护(≥0.08)
    """
    raw: dict[str, float] = {}
    for name in MODEL_NAMES:
        s = stats.get(name)
        if isinstance(s, dict) and int(s.get("samples") or 0) >= MIN_SAMPLES:
            try:
                acc = max(0.0, min(1.0, float(s.get("accuracy_pct") or 0.0) / 100.0))
            except (TypeError, ValueError):
                acc = 0.0
            raw[name] = acc ** 2  # 命中率平方, 强化优势模型
        else:
            raw[name] = 0.25  # 无数据默认
    return _normalize(raw)


def _normalize(raw: dict) -> dict:
    """归一化 + 权重下限保护(迭代重分配, 最终和=1)。"""
    total = sum(raw.values())
    if total <= 0:
        return dict(DEFAULT_MODEL_WEIGHTS)
    w = {k: v / total for k, v in raw.items()}
    # 迭代: 低于下限的模型提到 0.08, 差额按比例从高于下限的模型征收
    for _ in range(8):
        below = [k for k in w if w[k] < WEIGHT_FLOOR]
        if not below:
            break
        deficit = sum(WEIGHT_FLOOR - w[k] for k in below)
        above = [k for k in w if w[k] >= WEIGHT_FLOOR]
        above_total = sum(w[k] for k in above)
        if above_total <= 1e-12:  # 全部低于下限: 等分
            share = 1.0 / len(w)
            return {k: round(share, 4) for k in w}
        for k in below:
            w[k] = WEIGHT_FLOOR
        for k in above:
            w[k] -= deficit * w[k] / above_total
    total = sum(w.values())
    if total > 0:
        w = {k: v / total for k, v in w.items()}
    return {k: round(v, 4) for k, v in w.items()}


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------

def load_weights() -> dict:
    """加载模型权重: 历史命中率动态权重, 无数据时回退固定默认。

    优先级: DB backtest_results 聚合统计 → 最近回测落盘文件 → 默认权重。
    """
    global _last_source
    stats = _load_pooled_model_stats()
    if _has_usable_data(stats):
        _last_source = "history"
        return _compute_weights(stats)
    file_weights = _load_weights_file()
    if file_weights is not None:
        _last_source = "file"
        return file_weights
    _last_source = "default"
    return dict(DEFAULT_MODEL_WEIGHTS)


def _load_weights_file() -> dict | None:
    """读轻量权重文件(update_weights_after_backtest 写的兜底/审计记录)。"""
    if not os.path.exists(WEIGHTS_FILE):
        return None
    try:
        with open(WEIGHTS_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f)
        w = payload.get("weights")
        if not isinstance(w, dict) or not all(k in w for k in MODEL_NAMES):
            return None
        return {k: float(w[k]) for k in MODEL_NAMES}
    except (OSError, ValueError, TypeError, KeyError):
        return None


def update_weights_after_backtest(backtest_result: dict) -> dict:
    """回测后更新权重: 用最新 model_summary 计算权重并落盘(轻量 JSON 文件)。

    backtest_result: _do_backtest 算出的 model_summary
        {model_name: {"samples": int, "hits": int, "accuracy_pct": float}}
    返回计算出的权重(便于调用方日志)。
    """
    stats = _canonicalize_stats(backtest_result or {})
    weights = _compute_weights(stats)
    payload = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "backtest",
        "model_stats": stats,
        "weights": weights,
    }
    try:
        tmp = WEIGHTS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, WEIGHTS_FILE)  # 原子替换, 避免写一半
    except OSError:  # 落盘失败不阻断回测主流程
        pass
    return weights


def _canonicalize_stats(backtest_result: dict) -> dict:
    """把回测 model_summary 归一化: 遗留名映射 + 只保留 4 个当前模型。"""
    stats: dict[str, dict] = {}
    for raw_name, s in (backtest_result or {}).items():
        name = LEGACY_NAME_MAP.get(raw_name, raw_name)
        if name not in MODEL_NAMES or not isinstance(s, dict):
            continue
        try:
            samples = int(s.get("samples") or 0)
            hits = int(s.get("hits") or 0)
        except (TypeError, ValueError):
            continue
        if samples <= 0:
            continue
        stats[name] = {
            "samples": samples,
            "hits": hits,
            "accuracy_pct": round(hits / samples * 100, 1),
        }
    return stats


if __name__ == "__main__":
    w = load_weights()
    print(f"权重来源: {last_weights_source()}")
    print(w)
