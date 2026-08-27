# -*- coding: utf-8 -*-
"""
data_sources/qmt.py
未来 QMT / miniQMT(xtquant) 行情适配器占位。

V1.1 阶段仅预留接口与类结构, 不实际连接券商。
接入时:
  1. 安装 xtquant (miniQMT 提供)
  2. 实现 fetch_snapshot() 从 xtdata 订阅 510500 期权合约实时行情
  3. 返回与 base.MarketSnapshot 一致的契约结构, 引擎即可无缝切换
"""
from __future__ import annotations

import datetime as dt
from typing import List

from core.contracts import Contract
from data_sources.base import MarketDataSource, MarketSnapshot


class QmtDataSource(MarketDataSource):
    name = "qmt"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._connected = False

    def connect(self) -> None:
        """V1.1 未实现: 需要用户已安装 xtquant 且已完成 miniQMT 登录。
        未来版本在这里做: from xtquant import xtdata; xtdata.subscribe_quote(...)
        """
        raise NotImplementedError(
            "QMT 适配器为 V1.1 预留, 未实现。请继续使用 akshare_sina 免费行情; "
            "未来接入需安装 xtquant 并配置券商账号。"
        )

    def fetch_snapshot(self) -> MarketSnapshot:
        raise NotImplementedError(
            "QMT 适配器未实现。当前请使用 data_source.primary = akshare_sina"
        )

    def status(self) -> dict:
        return {"name": self.name, "ok": False, "note": "V1.1 预留, 未接入"}
