# -*- coding: utf-8 -*-
"""
data_sources/eastmoney.py
备用免费数据源(东方财富)。

注意: 东财期权免费接口主要提供日频/分时行情与期权分析,
不保证提供与新浪一致的实时五档盘口 Bid1/Ask1。
本适配器为"尽力而为"实现, 优先尝试东财公开接口;
若盘口字段不可用则明确降级(标记 valid=False 并给出原因),
保证引擎与评分逻辑不因数据源切换而崩溃。

真实盘口以 akshare_sina 为权威; 本模块用于断线时兜底或二次校验。
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import List, Optional

from core.contracts import Contract, validate_contract
from data_sources.base import MarketDataSource, MarketSnapshot

log = logging.getLogger("scanner.source")


class EastmoneyDataSource(MarketDataSource):
    name = "eastmoney"

    def __init__(self, cfg: dict, underlying_cfg: Optional[dict] = None):
        ds = cfg.get("data_source", {})
        uc = underlying_cfg or {}
        self.underlying = str(uc.get("spot_code", ds.get("underlying", "510500")))
        self.underlying_name = str(uc.get("name", ds.get("underlying_symbol", "ETF")))
        self.timeout = float(ds.get("request_timeout", 8))
        self.max_retries = int(ds.get("max_retries", 2))
        self._ak = None
        self._last_ok_at = None
        self._failures = 0

    def _akshare(self):
        if self._ak is None:
            try:
                import akshare as ak
            except ImportError as e:
                raise RuntimeError("缺少 akshare 依赖") from e
            self._ak = ak
        return self._ak

    # ------------------------------------------------------------------
    def fetch_snapshot(self) -> MarketSnapshot:
        now = dt.datetime.now()
        try:
            ak = self._akshare()
            # 东财当日期权行情(覆盖全部标的一起返回, 需过滤)
            df = ak.option_current_em()
            # 过滤 510500 认沽, 聚合到期日
            df = df[df["标的代码"].astype(str) == self.underlying]
            df = df[df["期权类型"] == "认沽"]
            if df.empty:
                raise RuntimeError("东财未返回 510500 认沽数据")

            contracts: List[Contract] = []
            for _, row in df.iterrows():
                strike = float(row.get("行权价", 0) or 0)
                if strike <= 0:
                    continue
                bid = float(row.get("买一价", 0) or 0)
                ask = float(row.get("卖一价", 0) or 0)
                name = str(row.get("合约简称", "") or "")
                expiry = str(row.get("到期日", "") or "")[:10]
                c = Contract(
                    code=str(row.get("合约代码", "") or ""),
                    name=name, trade_code=str(row.get("合约代码", "") or ""),
                    cp="P", strike=strike, expiry_date=expiry,
                    days_to_expiry=_days(expiry),
                    is_adjusted="A" in name.upper(),
                    bid=bid, ask=ask,
                    bid_vol=int(row.get("买一量", 0) or 0),
                    ask_vol=int(row.get("卖一量", 0) or 0),
                    last=float(row.get("最新价", 0) or 0),
                    volume=int(row.get("成交量", 0) or 0),
                    open_interest=int(row.get("持仓量", 0) or 0),
                    quote_time=now.strftime("%Y-%m-%d %H:%M:%S"),
                    fetched_at=now.strftime("%Y-%m-%d %H:%M:%S"),
                )
                if bid <= 0 or ask <= 0:
                    c.valid = False
                    c.invalid_reason = "东财盘口缺失"
                validate_contract(c)
                contracts.append(c)

            spot = float(df.iloc[0].get("标的价格", 0) or 0)
            self._last_ok_at = now
            self._failures = 0
            return MarketSnapshot(
                source=self.name, fetched_at=now.strftime("%Y-%m-%d %H:%M:%S"),
                spot=spot, spot_time=now.strftime("%Y-%m-%d %H:%M:%S"),
                contracts=contracts, target_months=[], fresh=True,
                underlying=self.underlying, underlying_name=self.underlying_name,
            )
        except Exception as e:
            self._failures += 1
            log.error("东财行情失败(连续%d次): %s", self._failures, e)
            return MarketSnapshot(
                source=self.name, fetched_at=now.strftime("%Y-%m-%d %H:%M:%S"),
                spot=0.0, contracts=[], fresh=False, error=str(e),
                underlying=self.underlying, underlying_name=self.underlying_name,
            )

    def status(self) -> dict:
        return {
            "name": self.name,
            "ok": self._failures == 0 and self._last_ok_at is not None,
            "consecutive_failures": self._failures,
            "note": "备用免费源, 盘口字段可能不完整",
        }


def _days(expiry: str) -> int:
    try:
        ed = dt.datetime.strptime(expiry[:10], "%Y-%m-%d").date()
        return (ed - dt.date.today()).days
    except Exception:
        return 0
