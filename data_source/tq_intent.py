# -*- coding: utf-8 -*-
"""TQ 主力意图资金 · 规则引擎(七口诀代码化)
==========================================

用户实战七口诀(位置优先 + 量价结合), 全部阈值来自配置文件(mi1_config.json
的 rules 段), 代码零硬编码:

    1. 外盘大 + 涨 + 放量          = 真金进攻            (强多, 5)
    2. 内盘大 + 跌 + 放量          = 主力撤退            (偏空, 2)
    3. 外盘大 + 跌 + 高位          = 诱多出货            (偏空, 2)
    4. 内盘大 + 涨 + 低位          = 压盘吸筹            (偏多, 4)
    5. 内外相当 + 横盘             = 多空平衡            (中性, 3)
    6. 内外双小                    = 控盘洗盘            (中性, 3)
    7. 内外双大 + 不动             = 对倒造假            (危险, 1)

叠加主力结构检测: 超大单净买 + 大单净卖 = 托盘出货(危险, 置顶覆盖),
依赖 L2AMO 分档数据, 缺数据时证据链如实标注"数据不足"。

评级 5 档: 5 强多 / 4 偏多 / 3 中性观望 / 2 偏空 / 1 危险。
多规则同时命中时按 RULE_PRIORITY 位置优先取最严评级, 证据链保留全部命中。

输出证据链(evidence)为 list[dict]: 哪个规则、哪几个字段、什么值——
LLM 综合研判只需消费该结构化证据, 规则引擎不做任何自然语言发挥。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 评级标签
RATING_LABELS = {
    5: "强多·真金进攻",
    4: "偏多·压盘吸筹",
    3: "中性·观望",
    2: "偏空·主力退却",
    1: "危险·对倒/出货",
}

# 位置优先: 数字小者胜出(危险信号置顶, 进攻次之, 中性兜底)
RULE_PRIORITY = {
    "duidao_fake": 1,       # 对倒造假
    "tuopan_dump": 1,       # 托盘出货(叠加)
    "zhenjin_attack": 2,    # 真金进攻
    "zhuli_retreat": 3,     # 主力撤退
    "yduo_dump": 4,         # 诱多出货
    "yapan_absorb": 5,      # 压盘吸筹
    "duokong_balance": 8,   # 多空平衡
    "kongpan_wash": 9,      # 控盘洗盘
}

RULE_LABELS = {
    "zhenjin_attack": "外盘大+涨+放量=真金进攻",
    "zhuli_retreat": "内盘大+跌+放量=主力撤退",
    "yduo_dump": "外盘大+跌+高位=诱多出货",
    "yapan_absorb": "内盘大+涨+低位=压盘吸筹",
    "duokong_balance": "内外相当+横盘=多空平衡",
    "kongpan_wash": "内外双小=控盘洗盘",
    "duidao_fake": "内外双大+不动=对倒造假",
    "tuopan_dump": "超大单买+大单卖=托盘出货(叠加)",
}

RULE_RATINGS = {
    "zhenjin_attack": 5,
    "yapan_absorb": 4,
    "duokong_balance": 3,
    "kongpan_wash": 3,
    "zhuli_retreat": 2,
    "yduo_dump": 2,
    "duidao_fake": 1,
    "tuopan_dump": 1,
}


def _f(row: Dict[str, Any], key: str, default: float = 0.0) -> float:
    v = row.get(key)
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def evaluate_rules(row: Dict[str, Any], rules_cfg: Dict[str, Any]) -> Dict[str, Any]:
    """对一行标准化数据跑七口诀, 返回评级与证据链。

    :param row: tq_main_force.build_main_force_row 的输出
    :param rules_cfg: mi1_config.json 的 rules 段(全部阈值)
    :return: {"rating": int, "label": str, "matched": [rule...],
              "evidence": [dict...], "features": {...}}
    """
    inside = _f(row, "内盘")
    outside = _f(row, "外盘")
    total = inside + outside
    zaf = _f(row, "涨幅")
    vol_ratio = _f(row, "量比")
    outer_ratio = (outside / total) if total > 0 else 0.5
    pos = row.get("60日分位")

    outer_dom = outer_ratio > 0.5 + float(rules_cfg["adv_ratio"])
    inner_dom = outer_ratio < 0.5 - float(rules_cfg["adv_ratio"])
    balanced = abs(outer_ratio - 0.5) <= float(rules_cfg["balance_tol"])
    up = zaf > 0
    down = zaf < 0
    flat = abs(zaf) <= float(rules_cfg["flat_pct"])
    vol_active = vol_ratio >= float(rules_cfg["vol_ratio_active"])
    vol_big = vol_ratio >= float(rules_cfg["vol_ratio_big"])
    vol_small = vol_ratio <= float(rules_cfg["vol_ratio_small"])
    pos_high = pos is not None and pos >= float(rules_cfg["pos_high"])
    pos_low = pos is not None and pos <= float(rules_cfg["pos_low"])

    features = {
        "外盘占比": round(outer_ratio, 4),
        "涨幅%": zaf,
        "量比": vol_ratio,
        "60日分位": pos,
        "内盘手": inside,
        "外盘手": outside,
    }

    matched: List[str] = []
    evidence: List[Dict[str, Any]] = []

    def hit(rule: str, cond: bool, logic: str) -> None:
        if cond:
            matched.append(rule)
            evidence.append({"rule": rule, "口诀": RULE_LABELS[rule], "判定逻辑": logic,
                             "字段值": dict(features)})

    # 1. 真金进攻: 外盘大 + 涨 + 放量
    hit("zhenjin_attack", outer_dom and up and vol_active,
        f"外盘占比{outer_ratio:.2f}>0.5+{rules_cfg['adv_ratio']} 且 涨幅{zaf}>0 且 量比{vol_ratio}>={rules_cfg['vol_ratio_active']}")
    # 2. 主力撤退: 内盘大 + 跌 + 放量
    hit("zhuli_retreat", inner_dom and down and vol_active,
        f"外盘占比{outer_ratio:.2f}<0.5-{rules_cfg['adv_ratio']} 且 跌 且 放量")
    # 3. 诱多出货: 外盘大 + 跌 + 高位(位置优先)
    hit("yduo_dump", outer_dom and down and pos_high,
        f"外盘大 且 跌 且 60日分位{pos}>={rules_cfg['pos_high']}")
    # 4. 压盘吸筹: 内盘大 + 涨 + 低位(位置优先)
    hit("yapan_absorb", inner_dom and up and pos_low,
        f"内盘大 且 涨 且 60日分位{pos}<={rules_cfg['pos_low']}")
    # 5. 多空平衡: 内外相当 + 横盘
    hit("duokong_balance", balanced and flat,
        f"|外盘占比-0.5|<={rules_cfg['balance_tol']} 且 |涨幅|<={rules_cfg['flat_pct']}%")
    # 6. 控盘洗盘: 内外双小
    hit("kongpan_wash", vol_small,
        f"量比{vol_ratio}<={rules_cfg['vol_ratio_small']}(内外双小)")
    # 7. 对倒造假: 内外双大 + 不动(横盘)
    hit("duidao_fake", vol_big and flat,
        f"量比{vol_ratio}>={rules_cfg['vol_ratio_big']}(双大) 且 横盘|涨幅|<={rules_cfg['flat_pct']}%")

    # 叠加: 托盘出货(超大单净买 + 大单净卖) — 依赖 L2AMO 分档
    xl, d = _f(row, "超大单净额", float("nan")), _f(row, "大单净额", float("nan"))
    if xl != xl or d != d:  # NaN = 分档数据缺失
        evidence.append({"rule": "tuopan_dump", "口诀": RULE_LABELS["tuopan_dump"],
                         "判定逻辑": "需要 L2AMO 分档数据", "字段值": "数据不足(公式接口待接入)"})
    elif xl > 0 and d < 0:
        matched.append("tuopan_dump")
        evidence.append({"rule": "tuopan_dump", "口诀": RULE_LABELS["tuopan_dump"],
                         "判定逻辑": "超大单净买+大单净卖(危险)",
                         "字段值": {"超大单净额": xl, "大单净额": d}})

    if not matched:
        # 七口诀全不命中: 落中性观望, 证据链说明原因
        evidence.append({"rule": "none", "口诀": "无命中",
                         "判定逻辑": "七口诀条件均未满足(量价中性且位置中性)",
                         "字段值": dict(features)})

    # 位置优先: 取优先级数字最小的命中规则作为最终评级
    top = sorted(matched or ["none"], key=lambda r: RULE_PRIORITY.get(r, 99))[0]
    rating = RULE_RATINGS.get(top, 3) if matched else 3
    return {
        "rating": rating,
        "label": RATING_LABELS[rating],
        "matched": matched,
        "evidence": evidence,
        "features": features,
    }
