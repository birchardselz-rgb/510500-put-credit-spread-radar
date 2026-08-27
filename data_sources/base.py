# -*- coding: utf-8 -*-
"""
data_sources/base.py
行情数据源抽象基类。所有数据源(免费/模拟/未来 QMT)实现同一接口,
便于引擎层无缝切换。
"""
from __future__ import annotations

import abc
import datetime as dt
from dataclasses import dataclass, field
from typing import List, Optional

from core.contracts import Contract


@dataclass
class MarketSnapshot:
    """一次行情采集的完整快照"""
    source: str
    fetched_at: str
    spot: float
    spot_time: Optional[str] = None
    contracts: List[Contract] = field(default_factory=list)
    target_months: List[str] = field(default_factory=list)
    fresh: bool = True
    error: Optional[str] = None
    underlying: str = ""        # 标的代码, 如 510500 / 588080
    underlying_name: str = ""   # 标的名称

    def contract_count(self) -> int:
        return len(self.contracts)


class MarketDataSource(abc.ABC):
    """行情数据源接口"""

    name = "base"
    underlying = ""
    underlying_name = ""

    @abc.abstractmethod
    def fetch_snapshot(self) -> MarketSnapshot:
        """采集一次完整快照: 标的价格 + 目标到期月份全部认沽合约盘口"""
        raise NotImplementedError

    def status(self) -> dict:
        """数据源状态(供看板展示)"""
        return {"name": self.name, "ok": True}

    def close(self) -> None:
        """释放资源"""
        pass
