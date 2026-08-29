# -*- coding: utf-8 -*-
"""
core/bs.py
Black-Scholes 定价与概率工具 —— 专业期权度量的核心引擎。

把免费行情升级为专业概率交易度量（Tastytrade 体系）：
  - implied_vol        : 从期权市价反推 IV（二分法，深交所无 Greeks 时启用）
  - pop_expire_above   : 到期时标的价格高于某水平（如盈亏平衡点）的概率 = 盈利概率 POP
  - delta_bs           : BS Delta（与行情源 Delta 交叉校验）
  - bs_price           : 理论价
  - dte_to_T           : 剩余自然日 -> 年化时间

公式（Put 信用价差盈利概率）：
  卖 Put 到期盈利当且仅当 现货到期 > 盈亏平衡点 BE = 卖出行权价 - credit
  在 lognormal 模型下：
    d2 = [ln(spot/BE) + (r - 0.5σ²)·T] / (σ·√T)
    POP = N(d2)
"""
from __future__ import annotations

import math
from statistics import NormalDist
from typing import Optional

_N = NormalDist()  # 标准正态分布

DEFAULT_RISK_FREE = 0.02  # 无风险利率(年化), 可用配置覆盖


def norm_cdf(x: float) -> float:
    return _N.cdf(x)


def dte_to_T(days: int) -> float:
    """剩余自然日 -> 年化时间（365 天制）"""
    if not days or days <= 0:
        return 0.0
    return days / 365.0


def bs_price(S: float, K: float, T: float, r: float, sigma: float, cp: str = "P") -> float:
    """Black-Scholes 期权理论价。cp: 'C'/'P'"""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    sq = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sq)
    d2 = d1 - sigma * sq
    if cp.upper() == "C":
        return S * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)
    return K * math.exp(-r * T) * norm_cdf(-d2) - S * norm_cdf(-d1)


def implied_vol(
    S: float, K: float, T: float, r: float, price: float, cp: str = "P",
    lo: float = 1e-4, hi: float = 5.0, tol: float = 1e-6,
) -> Optional[float]:
    """从期权市价反推隐含波动率（二分法）。无法反推返回 None。"""
    if price <= 0 or T <= 0 or S <= 0 or K <= 0:
        return None
    intrinsic = max(K - S, 0.0) if cp.upper() == "P" else max(S - K, 0.0)
    if price < intrinsic:  # 市价低于内在价值: 无意义
        return None
    for _ in range(200):
        mid = (lo + hi) / 2.0
        p = bs_price(S, K, T, r, mid, cp)
        if abs(p - price) < tol:
            return mid
        if p < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def delta_bs(S: float, K: float, T: float, r: float, sigma: float, cp: str = "P") -> Optional[float]:
    """BS Delta（期权对标的的一阶敏感度，Put 为负）"""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return None
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    if cp.upper() == "C":
        return norm_cdf(d1)
    return norm_cdf(d1) - 1.0


def pop_expire_above(
    spot: float, level: float, T: float, sigma: float, r: float = DEFAULT_RISK_FREE
) -> Optional[float]:
    """到期时现货 > level 的概率（lognormal 模型）。

    对 Put 信用价差：level = 盈亏平衡点 BE，返回即为盈利概率 POP。
    """
    if level <= 0 or spot <= 0 or T <= 0 or sigma <= 0:
        return None
    d2 = (math.log(spot / level) + (r - 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    return norm_cdf(d2)
