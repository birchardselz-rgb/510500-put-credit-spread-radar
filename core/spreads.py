# -*- coding: utf-8 -*-
"""
core/spreads.py
Put 信用价差生成与收益/风险计算模块。

核心原则:
- Put 信用价差 = 卖出高执行价 Put(Sell腿) + 买入低执行价 Put(Buy腿)
- 保守可成交净收 credit = SellPut.Bid1 - BuyPut.Ask1  (不用最新价冒充)
- credit <= 0 直接过滤
- 同时保存 mid-price 理论净收, 仅用于比较, 不用于报警成交标准
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import List, Optional

from core.contracts import Contract


@dataclass
class Spread:
    """一个 Put 信用价差组合"""
    sell: Contract                 # 卖出腿(高行权价)
    buy: Contract                  # 买入腿(低行权价)
    width: float                   # 执行价宽度
    credit: float = 0.0            # 可成交净收 = sell.Bid1 - buy.Ask1
    mid_credit: float = 0.0        # mid 理论净收(仅比较)
    max_profit: float = 0.0        # 最大盈利(元/组)
    max_loss: float = 0.0          # 最大亏损(元/组)
    breakeven: float = 0.0         # 到期盈亏平衡点
    safety_margin: float = 0.0     # 安全垫 (spot-BE)/spot
    reward_risk: float = 0.0       # 收益/风险比 最大盈利/最大亏损
    sell_slippage: float = 0.0     # 卖出腿盘口宽度
    buy_slippage: float = 0.0      # 买入腿盘口宽度
    total_slippage: float = 0.0    # 组合总滑点(两腿盘口宽度之和)
    valid: bool = True
    invalid_reason: str = ""
    # 计算时填写的上下文
    spot: float = 0.0
    multiplier: int = 10000
    underlying: str = ""           # 标的代码 510500 / 588080

    @property
    def sell_strike(self) -> float:
        return self.sell.strike

    @property
    def buy_strike(self) -> float:
        return self.buy.strike

    @property
    def expire_month(self) -> str:
        return (self.sell.expiry_date or "")[:7]

    @property
    def label(self) -> str:
        """组合显示名, 如 7.75/7.50P"""
        return f"{self.sell.strike:.2f}/{self.buy.strike:.2f}P"

    def to_dict(self) -> dict:
        return {
            "sell_code": self.sell.code, "buy_code": self.buy.code,
            "sell_strike": self.sell.strike, "buy_strike": self.buy.strike,
            "sell_name": self.sell.name, "buy_name": self.buy.name,
            "width": self.width, "credit": self.credit, "mid_credit": self.mid_credit,
            "max_profit": self.max_profit, "max_loss": self.max_loss,
            "breakeven": self.breakeven, "safety_margin": self.safety_margin,
            "reward_risk": self.reward_risk, "total_slippage": self.total_slippage,
            "sell_delta": self.sell.delta, "sell_iv": self.sell.iv,
            "buy_delta": self.buy.delta, "buy_iv": self.buy.iv,
            "expire_month": self.expire_month, "label": self.label,
            "valid": self.valid, "invalid_reason": self.invalid_reason,
            "underlying": self.underlying,
        }


def generate_put_credit_spreads(
    puts: List[Contract],
    widths: List[float],
    spot: float,
    multiplier: int = 10000,
    min_credit: float = 0.0,
    underlying: str = "",
) -> List[Spread]:
    """遍历合约链自动生成 Put 信用价差。

    - 卖出腿执行价 > 买入腿执行价
    - 执行价差 ∈ widths(如 [0.25, 0.50])
    - 同一到期月份内配对
    - credit <= 0 的组合直接过滤
    """
    valid_puts = [p for p in puts if p.valid and not p.is_adjusted]
    # 按到期月份分组
    by_month: dict = {}
    for p in valid_puts:
        by_month.setdefault(p.expiry_date, []).append(p)

    spreads: List[Spread] = []
    for expiry, group in by_month.items():
        for sell, buy in itertools.product(group, repeat=2):
            if sell.strike <= buy.strike:
                continue
            width = sell.strike - buy.strike
            # 浮点容差匹配(1.75-1.70=0.050000000000000044)
            if not any(abs(width - w) < 1e-6 for w in widths):
                continue
            sp = compute_spread(sell, buy, width, spot, multiplier, underlying=underlying)
            if sp.credit <= min_credit:
                sp.valid = False
                sp.invalid_reason = f"净收<=0({sp.credit:.4f})"
            spreads.append(sp)
    return spreads


def compute_spread(
    sell: Contract,
    buy: Contract,
    width: float,
    spot: float,
    multiplier: int = 10000,
    underlying: str = "",
) -> Spread:
    """计算单个价差的全部收益/风险指标。

    验收示例核对(spot=7.844):
        sell 7.75P Bid1=0.2055, buy 7.50P Ask1=0.1160
        credit    = 0.0895
        max_profit= 895
        max_loss  = (0.25-0.0895)*10000 = 1605
        breakeven = 7.75-0.0895 = 7.6605
        safety    = (7.844-7.6605)/7.844 = 2.34%
        reward_risk = 895/1605 = 55.76%
    """
    sp = Spread(sell=sell, buy=buy, width=round(width, 4), spot=spot,
                multiplier=multiplier, underlying=underlying)
    credit = sell.bid - buy.ask
    mid_credit = (sell.bid + sell.ask) / 2.0 - (buy.bid + buy.ask) / 2.0

    sp.credit = round(credit, 6)
    sp.mid_credit = round(mid_credit, 6)
    sp.max_profit = round(credit * multiplier, 2)
    sp.max_loss = round((width - credit) * multiplier, 2)
    sp.breakeven = round(sell.strike - credit, 6)
    sp.safety_margin = round((spot - sp.breakeven) / spot, 6) if spot > 0 else 0.0
    sp.reward_risk = round(sp.max_profit / sp.max_loss, 6) if sp.max_loss > 0 else 0.0
    sp.sell_slippage = round(sell.ask - sell.bid, 6)
    sp.buy_slippage = round(buy.ask - buy.bid, 6)
    sp.total_slippage = round(sp.sell_slippage + sp.buy_slippage, 6)
    return sp


def account_risk(spread: Spread, capital: float, lots: int) -> dict:
    """指定手数下占账户比例与最大亏损"""
    max_loss_total = spread.max_loss * lots
    return {
        "lots": lots,
        "max_loss_total": round(max_loss_total, 2),
        "pct_of_account": round(max_loss_total / capital, 6) if capital > 0 else 0.0,
    }


def suggested_lots(
    spread: Spread, capital: float, risk_budget_pct: float, cap: int = 100
) -> int:
    """按单批最大风险预算计算建议手数(不代表自动下单)。

    suggested_lots = floor(账户资金 * 风险预算比例 / 单组最大亏损), 上限 cap
    """
    budget = capital * risk_budget_pct
    if spread.max_loss <= 0:
        return 0
    lots = int(budget // spread.max_loss)
    return max(0, min(lots, cap))
