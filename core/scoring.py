# -*- coding: utf-8 -*-
"""
core/scoring.py
Put 信用价差 0~10 分综合评分模型。

维度: 净收 / 安全垫 / 收益风险 / 流动性(盘口) / 卖出Delta / 量能 / IV
权重合计 1.0, 各维度映射到 0~10 后加权平均。

硬性扣分: 盈亏平衡点接近或高于现价(安全垫不足)必须明显扣分,
不能因为收益/风险比很高就直接排第一。

分档:
  score < 5       跳过
  5   ~ 6.99     观察
  7   ~ 8.99     优质机会
  >= 9           强机会
"""
from __future__ import annotations

from typing import Dict

from core.spreads import Spread


def _lerp(x: float, x0: float, y0: float, x1: float, y1: float) -> float:
    """两点线性插值并夹到 [min(y0,y1), max(y0,y1)]"""
    if x1 == x0:
        return float(y1)
    v = y0 + (x - x0) * (y1 - y0) / (x1 - x0)
    lo, hi = min(y0, y1), max(y0, y1)
    return max(lo, min(hi, v))


def _clamp(v: float, lo: float = 0.0, hi: float = 10.0) -> float:
    return max(lo, min(hi, v))


# ---------------------------------------------------------------
# 各维度子评分(0~10)
# ---------------------------------------------------------------

def score_credit(credit: float, anchors: Dict[str, float]) -> float:
    """净收维度: 0.085->6, 0.095->8, 0.105->10"""
    a6 = anchors.get("a6", 0.085)
    a8 = anchors.get("a8", 0.095)
    a10 = anchors.get("a10", 0.105)
    if credit <= 0:
        return 0.0
    if credit <= a6:
        return _clamp(_lerp(credit, 0.0, 0.0, a6, 6.0))
    if credit <= a8:
        return _clamp(_lerp(credit, a6, 6.0, a8, 8.0))
    if credit <= a10:
        return _clamp(_lerp(credit, a8, 8.0, a10, 10.0))
    return 10.0


def score_safety(safety: float, anchors: Dict[str, float]) -> float:
    """安全垫维度: 2%->8, 4%->10; 安全垫<=0 -> 0"""
    a8 = anchors.get("a8", 0.02)
    a10 = anchors.get("a10", 0.04)
    if safety <= 0:
        return 0.0
    if safety <= a8:
        return _clamp(_lerp(safety, 0.0, 0.0, a8, 8.0))
    if safety <= a10:
        return _clamp(_lerp(safety, a8, 8.0, a10, 10.0))
    return 10.0


def score_reward_risk(rr: float, anchors: Dict[str, float]) -> float:
    """收益/风险维度: 0.5->8, 0.8->10"""
    a8 = anchors.get("a8", 0.50)
    a10 = anchors.get("a10", 0.80)
    if rr <= 0:
        return 0.0
    if rr <= a8:
        return _clamp(_lerp(rr, 0.0, 0.0, a8, 8.0))
    if rr <= a10:
        return _clamp(_lerp(rr, a8, 8.0, a10, 10.0))
    return 10.0


def score_liquidity(spread: Spread, max_ratio: float = 0.50) -> float:
    """流动性维度: 组合总滑点/净收 越小越好。

    滑点比 = total_slippage / credit
    < 0.15 -> 10; > max_ratio -> 2 线性回落
    """
    if spread.credit <= 0:
        return 0.0
    ratio = spread.total_slippage / spread.credit
    if ratio <= 0.15:
        return 10.0
    if ratio >= max_ratio:
        return _clamp(_lerp(ratio, max_ratio, 2.0, max_ratio * 2.0, 0.0))
    return _clamp(_lerp(ratio, 0.15, 10.0, max_ratio, 2.0))


def score_delta(delta_abs: float, dmin: float = 0.15, dmax: float = 0.35) -> float:
    """卖出腿 Delta 绝对值维度: 优选区间内 -> 10, 向外线性回落"""
    if delta_abs is None:
        return 5.0  # 无 Greeks 时给中性分
    if dmin <= delta_abs <= dmax:
        return 10.0
    if delta_abs < dmin:
        return _clamp(_lerp(delta_abs, 0.0, 2.0, dmin, 10.0))
    # delta_abs > dmax: 越虚越低分
    return _clamp(_lerp(delta_abs, dmax, 10.0, dmax + 0.30, 2.0))


def score_volume_oi(spread: Spread, min_vol: int = 20, min_oi: int = 100) -> float:
    """量能维度: 卖出腿 成交量/持仓量 越高越好"""
    vol = spread.sell.volume or 0
    oi = spread.sell.open_interest or 0
    vol_s = _clamp(_lerp(vol, min_vol, 2.0, min_vol * 20, 10.0))
    oi_s = _clamp(_lerp(oi, min_oi, 2.0, min_oi * 20, 10.0))
    return round((vol_s + oi_s) / 2.0, 4)


def score_iv(iv: float, good_min: float = 0.15, good_max: float = 0.45) -> float:
    """IV 维度: 合理区间(15%~45%)内给高分, 过高/过低回落"""
    if iv is None:
        return 5.0
    if good_min <= iv <= good_max:
        return 10.0
    if iv < good_min:
        return _clamp(_lerp(iv, 0.0, 3.0, good_min, 10.0))
    # iv > good_max
    return _clamp(_lerp(iv, good_max, 10.0, good_max + 0.30, 3.0))


# ---------------------------------------------------------------
# 综合评分
# ---------------------------------------------------------------

def score_spread(
    spread: Spread,
    weights: Dict[str, float],
    anchors: Dict[str, Dict[str, float]],
    min_safety: float = 0.02,
    delta_min: float = 0.15,
    delta_max: float = 0.35,
    iv_good_min: float = 0.15,
    iv_good_max: float = 0.45,
    max_slip_ratio: float = 0.50,
    min_vol: int = 20,
    min_oi: int = 100,
) -> Dict[str, float]:
    """返回 {score, credit, safety, reward_risk, liquidity, delta, volume_oi, iv, tier}"""
    credit_anchors = anchors.get("credit", {})
    safety_anchors = anchors.get("safety", {})
    rr_anchors = anchors.get("rr", {})

    c_credit = score_credit(spread.credit, credit_anchors)
    c_safety = score_safety(spread.safety_margin, safety_anchors)
    c_rr = score_reward_risk(spread.reward_risk, rr_anchors)
    c_liq = score_liquidity(spread, max_ratio=max_slip_ratio)
    c_delta = score_delta(abs(spread.sell.delta or 0.0), delta_min, delta_max)
    c_vol = score_volume_oi(spread, min_vol, min_oi)
    c_iv = score_iv(spread.sell.iv, iv_good_min, iv_good_max)

    total_w = sum(weights.values())
    if total_w <= 0:
        total_w = 1.0
    score = (
        weights.get("credit", 0.30) * c_credit
        + weights.get("safety", 0.25) * c_safety
        + weights.get("reward_risk", 0.15) * c_rr
        + weights.get("liquidity", 0.10) * c_liq
        + weights.get("delta", 0.10) * c_delta
        + weights.get("volume_oi", 0.05) * c_vol
        + weights.get("iv", 0.05) * c_iv
    ) / total_w

    # ---- 硬性扣分: 盈亏平衡点接近或高于现价 ----
    if spread.breakeven >= spread.spot:
        score = min(score, 0.5)      # BE 高于现价: 必然亏损
    elif spread.safety_margin <= 0:
        score = min(score, 1.0)
    elif spread.safety_margin < min_safety:
        # 安全垫不足: 按缺口线性扣分, 最高扣至 4 分(观察线以下)
        gap = (min_safety - spread.safety_margin) / min_safety
        score = score - 4.0 * gap

    score = _clamp(round(score, 4))
    return {
        "score": score,
        "c_credit": round(c_credit, 4),
        "c_safety": round(c_safety, 4),
        "c_reward_risk": round(c_rr, 4),
        "c_liquidity": round(c_liq, 4),
        "c_delta": round(c_delta, 4),
        "c_volume_oi": round(c_vol, 4),
        "c_iv": round(c_iv, 4),
        "tier": tier_of(score),
    }


def tier_of(score: float, observe: float = 5.0, good: float = 7.0, strong: float = 9.0) -> str:
    if score >= strong:
        return "强机会"
    if score >= good:
        return "优质机会"
    if score >= observe:
        return "观察"
    return "跳过"


def qualify_for_alert(spread: Spread, result: Dict[str, float], cfg) -> bool:
    """是否满足报警条件(净收与评分双门槛)"""
    min_credit = cfg["alerts"].get("min_credit_to_alert", 0.085)
    min_score = cfg["alerts"].get("min_score_to_alert", 7.0)
    return spread.credit >= min_credit and result["score"] >= min_score
