# -*- coding: utf-8 -*-
"""
data_sources/tonghuashun.py
同花顺优先适配器。

实测(2026-08-27):
- 同花顺 qd.10jqka.com.cn 实时快照需 JS 风控(401), 不可直接用
- 同花顺 d.10jqka.com.cn/v6/time/hs_XXXXXX/last.js 免费分时接口可用,
  标的现货价格与新浪完全一致: 510500=7.973 / 588080=1.732
- 同花顺免费 Web 接口不提供 ETF 期权链 Bid/Ask 五档盘口

因此本适配器: 标的现货优先用同花顺(v6/time), 失败自动回退新浪;
期权链(盘口/Greeks)使用新浪批量接口(免费源中唯一稳定的期权盘口源)。
数据源名称标记为 "tonghuashun"(现货来自同花顺) 或 "tonghuashun_sina"(回退)。
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
from typing import Optional

import requests

from data_sources.akshare_sina import AkshareSinaDataSource
from data_sources.base import MarketDataSource, MarketSnapshot

log = logging.getLogger("scanner.source")

THS_TIME_URL = "http://d.10jqka.com.cn/v6/time/hs_{code}/last.js"
THS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "http://www.10jqka.com.cn/",
}


class TonghuashunDataSource(MarketDataSource):
    """同花顺现货 + 新浪期权链。"""

    name = "tonghuashun"

    def __init__(self, cfg: dict, underlying_cfg: Optional[dict] = None):
        self.cfg = cfg
        self.underlying_cfg = underlying_cfg
        self._sina = AkshareSinaDataSource(cfg, underlying_cfg)
        self.underlying = self._sina.underlying
        self.underlying_name = self._sina.underlying_name
        self.timeout = self._sina.timeout
        self._spot_ok = False
        self._last_spot_source = ""

    # ------------------------------------------------------------------
    def fetch_snapshot(self) -> MarketSnapshot:
        spot, spot_time, ok = self._fetch_ths_spot()
        if ok:
            self._spot_ok = True
            self._last_spot_source = "tonghuashun"
            snap = self._sina.fetch_snapshot(
                spot_override=spot, spot_time_override=spot_time,
                source_name="tonghuashun",
            )
        else:
            self._spot_ok = False
            self._last_spot_source = "sina"
            log.warning("同花顺现货获取失败, 回退新浪现货")
            snap = self._sina.fetch_snapshot(source_name="tonghuashun_sina")
        return snap

    # ------------------------------------------------------------------
    def _fetch_ths_spot(self) -> tuple:
        """从同花顺 v6/time 获取标的现货(取分时最后一笔)。

        返回 (price, time_str, ok)。失败返回 (0.0, "", False)。
        """
        code = self.underlying
        try:
            r = requests.get(
                THS_TIME_URL.format(code=code), headers=THS_HEADERS, timeout=self.timeout
            )
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            m = re.search(r"\((.*)\)", r.text, re.S)
            if not m:
                raise RuntimeError("响应无 JSON")
            obj = json.loads(m.group(1))
            info = obj.get(f"hs_{code}")
            if not info or not info.get("data"):
                raise RuntimeError("无分时数据")
            last_pt = info["data"].rstrip(";").split(";")[-1].split(",")
            price = float(last_pt[1])
            hm = last_pt[0]
            date = str(info.get("date", ""))
            time_str = f"{date[:4]}-{date[4:6]}-{date[6:8]} {hm[:2]}:{hm[2:]}:00" if date else ""
            return price, time_str, True
        except Exception as e:
            log.warning("同花顺 %s 现货失败: %s", code, e)
            return 0.0, "", False

    # ------------------------------------------------------------------
    def status(self) -> dict:
        return {
            "name": self.name,
            "ok": self._spot_ok,
            "spot_source": self._last_spot_source or "unknown",
            "note": "现货=同花顺, 期权盘口=新浪(同花顺免费接口无期权五档)",
            "underlying": self.underlying,
        }

    def close(self) -> None:
        self._sina.close()
