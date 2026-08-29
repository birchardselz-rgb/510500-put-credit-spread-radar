# -*- coding: utf-8 -*-
"""
data_sources/mock.py
离线模拟数据源：无网络也能测试核心计算与报警链路。

支持多标的:
- 510500 (中证500ETF, 行权价间距 0.25):
    scenario=acceptance -> 内置用户验收示例
      510500=7.844, 7.75P Bid1=0.2055, 7.50P Ask1=0.1160
      => 可成交净收 0.0895, 最大盈利 895, 最大亏损 1605, BE 7.6605,
         安全垫 2.34%, 收益/风险 55.8%
- 588080 (科创50ETF易方达, 行权价间距 0.05): 围绕 1.732 生成合理链

每个标的生成 9 月 + 10 月 两个到期月份的链, 用于演示/测试多到期月扫描。
"""
from __future__ import annotations

import datetime as dt
import math
import random
from typing import List, Optional

from core.contracts import Contract
from data_sources.base import MarketDataSource, MarketSnapshot


def _mk_contract(
    code: int, strike: float, cp: str, name: str, trade_code: str,
    bid: float, ask: float, bid_vol: int, ask_vol: int, last: float,
    volume: int, oi: int, iv: float, delta: float, expiry: str,
    days: int, adjusted: bool = False, quote_time: str = "",
) -> Contract:
    return Contract(
        code=str(code), name=name, trade_code=trade_code, cp=cp, strike=strike,
        expiry_date=expiry, days_to_expiry=days, is_adjusted=adjusted,
        bid=bid, ask=ask, bid_vol=bid_vol, ask_vol=ask_vol, last=last,
        volume=volume, open_interest=oi,
        iv=iv, delta=delta, gamma=0.02, theta=-0.05, vega=0.03,
        quote_time=quote_time or dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        fetched_at=dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )


class MockDataSource(MarketDataSource):
    name = "mock"

    def __init__(self, cfg: dict, underlying_cfg: Optional[dict] = None):
        m = cfg.get("mock", {})
        self.scenario = m.get("scenario", "acceptance")
        self.expiry = m.get("expiry_date", "2026-09-23")       # 9 月
        self.days = int(m.get("days_to_expiry", 27))
        self.expiry2 = m.get("expiry_date2", "2026-10-28")     # 10 月
        self.days2 = int(m.get("days_to_expiry2", 62))
        self.expiry3 = m.get("expiry_date3", "2026-12-23")     # 12 月(季度)
        self.days3 = int(m.get("days_to_expiry3", 118))
        # 标的
        if underlying_cfg:
            self.underlying = str(underlying_cfg.get("spot_code", "510500"))
            self.underlying_name = str(underlying_cfg.get("name", "ETF"))
            self.multiplier = int(underlying_cfg.get("multiplier", 10000))
            widths = underlying_cfg.get("strike_widths", [0.25, 0.50])
            self.spacing = min((float(w) for w in widths), default=0.25)
        else:
            self.underlying = "510500"
            self.underlying_name = "中证500ETF"
            self.multiplier = 10000
            self.spacing = 0.25
        # 现货
        if self.underlying == "588080":
            self.spot = 1.732
            self._anchor_high, self._anchor_low = None, None
        else:
            self.spot = float(m.get("spot", 7.844))
            self._anchor_high = (7.75, 0.2055, 0.2075)
            self._anchor_low = (7.50, 0.1140, 0.1160)
        self._rng = random.Random(int(self.underlying))

    # ------------------------------------------------------------------
    def fetch_snapshot(self) -> MarketSnapshot:
        now = dt.datetime.now()
        contracts = self._build_chain()
        return MarketSnapshot(
            source=self.name,
            fetched_at=now.strftime("%Y-%m-%d %H:%M:%S"),
            spot=self.spot,
            spot_time=now.strftime("%Y-%m-%d %H:%M:%S"),
            contracts=contracts,
            target_months=["202609", "202610", "202612"],
            fresh=True,
            underlying=self.underlying, underlying_name=self.underlying_name,
        )

    # ------------------------------------------------------------------
    def _build_chain(self) -> List[Contract]:
        if self.underlying == "588080":
            return self._build_588080()
        return self._build_acceptance()

    def _build_acceptance(self) -> List[Contract]:
        """510500 验收示例链(行权价 0.25 间隔, 保证 0.25/0.50 宽度均可生成):
        9月: 7.75P bid=0.2055/ask=0.2075; 7.50P bid=0.1140/ask=0.1160
              => 7.75/7.50 价差 credit=0.2055-0.1160=0.0895
        10月: 同执行价, 权利金按时间价值上浮(多到期月演示)
        """
        spot = self.spot  # 7.844
        strikes = [8.00, 7.75, 7.50, 7.25, 7.00, 6.75, 6.50, 6.25,
                   6.00, 5.75, 5.50, 5.25, 5.00]
        strikes = [s for s in strikes if s > 0]
        anchor_high_bid, anchor_high_ask = 0.2055, 0.2075   # 7.75
        anchor_low_bid, anchor_low_ask = 0.1140, 0.1160     # 7.50
        puts = self._month_chain_510500(
            strikes, anchor_high_bid, anchor_high_ask, anchor_low_bid, anchor_low_ask,
            spot, expiry=self.expiry, days=self.days, month_label="9月",
            yymm="2609", code_base=10012000, scale=1.0,
        )
        # 10 月链: 更长时间价值 => 权利金整体上浮
        puts += self._month_chain_510500(
            strikes, anchor_high_bid, anchor_high_ask, anchor_low_bid, anchor_low_ask,
            spot, expiry=self.expiry2, days=self.days2, month_label="10月",
            yymm="2610", code_base=10022000, scale=1.30,
        )
        # 12 月(季度)链: 更长时间价值
        puts += self._month_chain_510500(
            strikes, anchor_high_bid, anchor_high_ask, anchor_low_bid, anchor_low_ask,
            spot, expiry=self.expiry3, days=self.days3, month_label="12月",
            yymm="2612", code_base=10032000, scale=1.50,
        )
        # A 类调整合约(应被过滤, 放在 9 月)
        puts.append(self._finalize_contract(
            10099999, 7.65, 0.1200, 0.1220, spot, adjusted=True))
        return puts

    def _month_chain_510500(
        self, strikes, anchor_high_bid, anchor_high_ask, anchor_low_bid,
        anchor_low_ask, spot, expiry, days, month_label, yymm,
        code_base, scale,
    ) -> List[Contract]:
        puts = []
        for i, k in enumerate(strikes):
            code = code_base + i
            if abs(k - 7.75) < 0.01:
                bid, ask = anchor_high_bid * scale, anchor_high_ask * scale
            elif abs(k - 7.50) < 0.01:
                bid, ask = anchor_low_bid * scale, anchor_low_ask * scale
            elif k > 7.75:
                mid = (anchor_high_bid + (k - 7.75) * 0.6
                       + self._rng.random() * 0.01) * scale
                bid, ask = mid - 0.0015, mid + 0.0015
            else:
                mid = anchor_low_bid * math.exp(-(7.50 - k) / 0.7) + self._rng.random() * 0.003
                mid = max(0.001, mid) * scale
                bid, ask = mid - 0.001, mid + 0.001
                if bid <= 0:
                    bid = 0.001
            bid = round(bid, 4)
            ask = round(max(ask, bid + 0.0005), 4)
            puts.append(self._finalize_contract(
                code, k, bid, ask, spot, adjusted=False,
                expiry=expiry, days=days, month_label=month_label, yymm=yymm,
            ))
        return puts

    def _build_588080(self) -> List[Contract]:
        """588080 模拟链: 使用实测真实认沽盘口(2026-08-27 收盘, spot=1.732,
        行权价 1.10~2.45, 间距 0.05, 高IV=>近ATM认沽权利金大), 生成 9月+10月"""
        spot = self.spot  # 1.732
        real = [  # (strike, bid, ask)
            (1.10, 0.0006, 0.0009), (1.15, 0.0009, 0.0011), (1.20, 0.0011, 0.0013),
            (1.25, 0.0016, 0.0018), (1.30, 0.0022, 0.0029), (1.35, 0.0034, 0.0037),
            (1.40, 0.0056, 0.0059), (1.45, 0.0092, 0.0093), (1.50, 0.0140, 0.0142),
            (1.55, 0.0196, 0.0207), (1.60, 0.0299, 0.0300), (1.65, 0.0441, 0.0450),
            (1.70, 0.0637, 0.0642), (1.75, 0.0890, 0.0900), (1.80, 0.1168, 0.1201),
            (1.85, 0.1548, 0.1597), (1.90, 0.1950, 0.2013), (1.95, 0.2357, 0.2455),
            (2.00, 0.2813, 0.2919), (2.05, 0.3281, 0.3392), (2.10, 0.3763, 0.3877),
            (2.15, 0.4247, 0.4364), (2.20, 0.4734, 0.4854), (2.25, 0.5225, 0.5350),
            (2.30, 0.5720, 0.5847), (2.35, 0.6215, 0.6344), (2.40, 0.6711, 0.6842),
            (2.45, 0.7204, 0.7338),
        ]
        puts = self._month_chain_588080(
            real, spot, expiry=self.expiry, days=self.days,
            month_label="9月", yymm="2609", code_base=20012000, scale=1.0,
        )
        puts += self._month_chain_588080(
            real, spot, expiry=self.expiry2, days=self.days2,
            month_label="10月", yymm="2610", code_base=20022000, scale=1.30,
        )
        puts += self._month_chain_588080(
            real, spot, expiry=self.expiry3, days=self.days3,
            month_label="12月", yymm="2612", code_base=20032000, scale=1.50,
        )
        # A 类调整合约(应被过滤, 放在 9 月)
        puts.append(self._finalize_contract(
            20099999, 1.70, 0.0350, 0.0360, spot, adjusted=True))
        return puts

    def _month_chain_588080(self, real, spot, expiry, days, month_label,
                            yymm, code_base, scale) -> List[Contract]:
        puts = []
        for i, (k, bid, ask) in enumerate(real):
            code = code_base + i
            puts.append(self._finalize_contract(
                code, k, bid * scale, ask * scale, spot, adjusted=False,
                expiry=expiry, days=days, month_label=month_label, yymm=yymm,
            ))
        return puts

    def _finalize_contract(self, code: int, k: float, bid: float, ask: float,
                           spot: float, adjusted: bool, expiry: Optional[str] = None,
                           days: Optional[int] = None, month_label: str = "9月",
                           yymm: str = "2609") -> Contract:
        expiry = expiry or self.expiry
        days = days or self.days
        vol = 300 + int(self._rng.random() * 2000)
        oi = 3000 + int(self._rng.random() * 20000)
        iv = round(0.22 + self._rng.random() * 0.10, 4)
        delta = round(-(0.05 + (spot - k) / spot * 0.6), 3)
        delta = max(-0.99, min(-0.001, delta))
        # 简称与交易代码
        if self.underlying == "588080":
            name = f"科创板50沽{month_label}{int(round(k*100)):g}" + ("A" if adjusted else "")
            prefix = f"588080P{yymm}M" if not adjusted else f"588080P{yymm}A"
        else:
            name = f"500ETF沽{month_label}{int(round(k*100)):g}" + ("A" if adjusted else "")
            prefix = f"510500P{yymm}M" if not adjusted else f"510500P{yymm}A"
        trade_code = f"{prefix}{int(round(k*1000)):05d}"
        return _mk_contract(
            code, k, "P", name, trade_code, bid, ask,
            int(self._rng.random() * 30) + 1, int(self._rng.random() * 30) + 1,
            round((bid + ask) / 2, 4), vol, oi, iv, delta,
            expiry, days, adjusted,
        )
