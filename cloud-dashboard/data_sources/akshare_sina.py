# -*- coding: utf-8 -*-
"""
data_sources/akshare_sina.py
免费实时行情主适配器（新浪 / AKShare）。支持上交所 + 深交所 ETF 期权。

已实测验证的链路(2026-08-27/28):
- 上交所到期月份: ak.option_sse_list_sina(symbol='500ETF') -> ['202609', ...]
- 上交所认沽代码: ak.option_sse_codes_sina(symbol='看跌期权', trade_date='202609', underlying='510500')
- 深交所合约表:   ak.option_current_day_szse()  -> 标的证券简称(代码)/合约类型/合约编码/到期日
                  (官方当日表, 覆盖 159901/159915/159919/159922)
- 盘口(批量, 沪深通用): https://hq.sinajs.cn/list=CON_OP_10012280,CON_OP_90007078,...
  Bid1=字段[22] Ask1=字段[20] 行权价=字段[7] 简称=字段[37]
  成交量=字段[41] 持仓量=字段[5] 行情时间=字段[32] 合约标识M/A=字段[43]
  到期日=字段[46] 剩余天数=字段[47]   (沪深字段布局一致, 已实测深市51字段)
- Greeks(批量, 仅上交所): https://hq.sinajs.cn/list=CON_SO_10012280,...
  Delta=字段[5] Gamma=[6] Theta=[7] Vega=[8] IV=[9] 交易代码=[12] 行权价=[13] 标识=[16]
  注意: 深交所 CON_SO_ 字段布局与上交所不同, 深市自动关闭 Greeks。
- 标的现价: https://hq.sinajs.cn/list=sh510500 / sz159915 -> 字段[3]=现价, [30]/[31]=日期时间

内置超时/重试/断线恢复/异常过滤/数据源状态。A 类调整合约在此层标记 is_adjusted。
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Dict, List, Optional

import requests

from core.contracts import Contract, is_adjusted_by_name, parse_trade_code, validate_contract
from data_sources.base import MarketDataSource, MarketSnapshot

log = logging.getLogger("scanner.source")

SINA_HQ = "https://hq.sinajs.cn"
SINA_HEADERS_OP = {
    "Referer": "https://stock.finance.sina.com.cn/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
}
SINA_HEADERS_SO = {
    "Referer": "https://vip.stock.finance.sina.com.cn/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
}

# 深交所合约表缓存时长(合约日内不变, 5 分钟刷新足够)
SZSE_TABLE_TTL = 300


class AkshareSinaDataSource(MarketDataSource):
    name = "akshare_sina"

    def __init__(self, cfg: dict, underlying_cfg: Optional[dict] = None):
        ds = cfg.get("data_source", {})
        # 兼容旧配置: 无 underlyings 时用 data_source.underlying 等
        self.underlying_cfg = underlying_cfg or {
            "name": ds.get("underlying_symbol", "中证500ETF"),
            "category": ds.get("contract_category", "500ETF"),
            "spot_code": ds.get("underlying", "510500"),
            "multiplier": 10000,
        }
        self.underlying = str(self.underlying_cfg.get("spot_code", "510500"))
        self.underlying_name = str(self.underlying_cfg.get("name", "ETF"))
        self.category = str(self.underlying_cfg.get("category", "500ETF"))
        # 交易所识别: 15xxxx=深交所(创业板/深300/深500/深100), 其余=上交所
        self.exchange = "sz" if str(self.underlying).startswith("15") else "sh"
        self._szse_cache: Optional[tuple] = None  # (timestamp, DataFrame) 深交所合约表缓存
        self.timeout = float(ds.get("request_timeout", 8))
        self.max_retries = int(ds.get("max_retries", 3))
        self.backoff = float(ds.get("retry_backoff_seconds", 1.0))
        self.max_batch = int(ds.get("max_batch", 40))
        self.stale_seconds = float(ds.get("quote_stale_seconds", 30))
        self.enable_greeks = bool(ds.get("enable_greeks", True))
        if self.exchange == "sz":
            # 深交所 CON_SO_ 字段布局与上交所不同, 不解析 Greeks(评分自动给中性分)
            self.enable_greeks = False
        self._session = requests.Session()
        self._last_ok_at: Optional[dt.datetime] = None
        self._consecutive_failures = 0
        self._ak = None

    # ------------------------------------------------------------------
    # AKShare 延迟导入
    # ------------------------------------------------------------------
    def _akshare(self):
        if self._ak is None:
            try:
                import akshare as ak
            except ImportError as e:
                raise RuntimeError(
                    "缺少 akshare 依赖, 请先执行: pip install akshare"
                ) from e
            self._ak = ak
        return self._ak

    # ------------------------------------------------------------------
    # 通用请求(带重试)
    # ------------------------------------------------------------------
    def _get(self, url: str, params: Optional[dict] = None, headers: Optional[dict] = None,
             retries: Optional[int] = None) -> requests.Response:
        retries = self.max_retries if retries is None else retries
        last_err: Optional[Exception] = None
        for i in range(retries):
            try:
                r = self._session.get(url, params=params, headers=headers, timeout=self.timeout)
                if r.status_code == 200:
                    return r
                last_err = RuntimeError(f"HTTP {r.status_code}")
            except requests.RequestException as e:
                last_err = e
            if i < retries - 1:
                time.sleep(self.backoff * (i + 1))
        raise last_err or RuntimeError("请求失败")

    # ------------------------------------------------------------------
    # 目标到期月份
    # ------------------------------------------------------------------
    def get_target_months(self) -> List[str]:
        """选择剩余天数在窗口内的到期月份。"""
        if self.exchange == "sz":
            return self._szse_target_months()
        ak = self._akshare()
        months = ak.option_sse_list_sina(symbol=self.category)
        log.info("新浪返回合约月份: %s", months)

        from core.contracts import fourth_wednesday, pick_next_expiry_months
        cf = cfg_contracts()
        dmin = int(cf.get("expire_days_min", 15))
        dmax = int(cf.get("expire_days_max", 45))
        preferred = cf.get("preferred_expire_month")
        count = int(cf.get("expire_months_count", 0) or 0)

        today = dt.date.today()
        # 优先: 扫描接下来 N 个到期月份(如 9 月+10 月), 不依赖天数窗口
        if count and count > 0:
            picked = pick_next_expiry_months(months, count, today)
            log.info("%s 按 expire_months_count=%d 选择到期月份: %s",
                     self.underlying, count, picked)
            if preferred and preferred in picked:
                return [preferred]
            if not picked:
                log.warning("按到期月数量无命中(可用月份: %s)", months)
            return picked

        # 默认: 天数窗口
        picked = []
        for m in months:
            m = m.strip()
            if len(m) != 6 or not m.isdigit():
                continue
            year, month = int(m[:4]), int(m[4:])
            try:
                expire = fourth_wednesday(year, month)
            except ValueError:
                continue
            remain = (expire - today).days
            if dmin <= remain <= dmax:
                picked.append(m)
        picked = sorted(picked)

        if preferred and preferred in picked:
            return [preferred]
        if not picked:
            log.warning("本地计算无命中月份(当前月份: %s, 窗口 %d~%d 天)",
                        months, dmin, dmax)
        return picked

    # ------------------------------------------------------------------
    def _szse_df(self) -> Optional[object]:
        """深交所当日认沽合约表(缓存 SZSE_TABLE_TTL 秒)。返回 pandas.DataFrame 或 None。"""
        if self._szse_cache is not None:
            ts, df = self._szse_cache
            if (dt.datetime.now() - ts).total_seconds() < SZSE_TABLE_TTL:
                return df
        ak = self._akshare()
        try:
            df = ak.option_current_day_szse()
        except Exception as e:
            log.warning("深交所合约表获取失败: %s", e)
            if self._szse_cache is not None:
                return self._szse_cache[1]  # 用旧缓存兜底
            return None
        col = "标的证券简称(代码)"
        if col not in df.columns:
            cols = [c for c in df.columns if "标的" in c]
            col = cols[0] if cols else None
        if col is None or "合约类型" not in df.columns or "到期日" not in df.columns:
            log.warning("深交所合约表列异常: %s", list(df.columns))
            return None
        df = df[df[col].astype(str).str.contains(str(self.underlying), regex=False)]
        df = df[df["合约类型"] == "认沽"]
        self._szse_cache = (dt.datetime.now(), df)
        return df

    # ------------------------------------------------------------------
    def _szse_target_months(self) -> List[str]:
        """深交所: 从官方当日合约表枚举目标到期月份(YYYYMM)。"""
        df = self._szse_df()
        if df is None or len(df) == 0:
            log.warning("深市 %s 未获取到认沽合约表", self.underlying)
            return []
        months = sorted({str(x)[:7].replace("-", "") for x in df["到期日"].astype(str)})
        from core.contracts import fourth_wednesday, pick_next_expiry_months
        cf = cfg_contracts()
        dmin = int(cf.get("expire_days_min", 15))
        dmax = int(cf.get("expire_days_max", 45))
        preferred = cf.get("preferred_expire_month")
        count = int(cf.get("expire_months_count", 0) or 0)
        today = dt.date.today()

        if count and count > 0:
            picked = pick_next_expiry_months(months, count, today)
            if preferred and preferred in picked:
                return [preferred]
            if not picked:
                log.warning("深市 %s 按到期月数量无命中(月份: %s)", self.underlying, months)
            return picked

        picked = []
        for m in months:
            m = m.strip()
            if len(m) != 6 or not m.isdigit():
                continue
            year, month = int(m[:4]), int(m[4:])
            try:
                expire = fourth_wednesday(year, month)
            except ValueError:
                continue
            remain = (expire - today).days
            if dmin <= remain <= dmax:
                picked.append(m)
        picked = sorted(picked)
        if preferred and preferred in picked:
            return [preferred]
        if not picked:
            log.warning("深市 %s 天数窗口无命中(月份: %s)", self.underlying, months)
        return picked

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------
    def fetch_snapshot(self, spot_override: Optional[float] = None,
                       spot_time_override: Optional[str] = None,
                       source_name: Optional[str] = None) -> MarketSnapshot:
        now = dt.datetime.now()
        try:
            months = self.get_target_months()
            if not months:
                raise RuntimeError(
                    f"{self.underlying_name} 无剩余 "
                    f"{cfg_contracts().get('expire_days_min',15)}~"
                    f"{cfg_contracts().get('expire_days_max',45)} 天到期的标准合约月份"
                )
            codes = self._fetch_put_codes(months)
            if not codes:
                raise RuntimeError(f"{self.underlying_name} 月份 {months} 未获取到认沽合约")
            quotes = self._fetch_quotes(codes)
            greeks = self._fetch_greeks(codes) if self.enable_greeks else {}
            if spot_override is not None:
                spot, spot_time = spot_override, (spot_time_override or "")
            else:
                spot, spot_time = self._fetch_spot()
            contracts = self._assemble(quotes, greeks, spot_time)

            self._last_ok_at = now
            self._consecutive_failures = 0
            fresh = self._is_fresh(now, spot_time, contracts)
            return MarketSnapshot(
                source=source_name or self.name,
                fetched_at=now.strftime("%Y-%m-%d %H:%M:%S"),
                spot=spot, spot_time=spot_time, contracts=contracts,
                target_months=months, fresh=fresh,
                underlying=self.underlying, underlying_name=self.underlying_name,
            )
        except Exception as e:
            self._consecutive_failures += 1
            log.error("%s 行情采集失败(连续%d次): %s",
                      self.underlying_name, self._consecutive_failures, e)
            return MarketSnapshot(
                source=source_name or self.name,
                fetched_at=now.strftime("%Y-%m-%d %H:%M:%S"),
                spot=0.0, contracts=[], target_months=[], fresh=False,
                error=str(e), underlying=self.underlying,
                underlying_name=self.underlying_name,
            )

    # ------------------------------------------------------------------
    def _fetch_put_codes(self, months: List[str]) -> List[str]:
        if self.exchange == "sz":
            df = self._szse_df()
            if df is None or len(df) == 0:
                return []
            sub = df[df["到期日"].astype(str).str[:7].str.replace("-", "", regex=False).isin(months)]
            codes = [str(c) for c in sub["合约编码"].tolist() if str(c).strip()]
            log.info("深市 %s 认沽合约数: %d (月份 %s)", self.underlying, len(codes), months)
            return codes
        ak = self._akshare()
        codes: List[str] = []
        for m in months:
            df = ak.option_sse_codes_sina(symbol="看跌期权", trade_date=m, underlying=self.underlying)
            for c in df["期权代码"].tolist():
                c = str(c).strip()
                if c and c not in codes:
                    codes.append(c)
        log.info("认沽合约数: %d (月份 %s)", len(codes), months)
        return codes

    # ------------------------------------------------------------------
    def _fetch_quotes(self, codes: List[str]) -> Dict[str, list]:
        """批量获取盘口, 返回 {code: fields_list}(code 已去掉 CON_OP_ 前缀)"""
        result: Dict[str, list] = {}
        for i in range(0, len(codes), self.max_batch):
            batch = codes[i:i + self.max_batch]
            url = SINA_HQ + "/list=" + ",".join(f"CON_OP_{c}" for c in batch)
            r = self._get(url, headers=SINA_HEADERS_OP)
            for k, v in _parse_sina_var(r.text).items():
                result[k.replace("CON_OP_", "")] = v
        return result

    # ------------------------------------------------------------------
    def _fetch_greeks(self, codes: List[str]) -> Dict[str, list]:
        """批量获取 Greeks(仅上交所启用); 失败时快速降级(不重试拖慢扫描)"""
        result: Dict[str, list] = {}
        try:
            for i in range(0, len(codes), self.max_batch):
                batch = codes[i:i + self.max_batch]
                url = SINA_HQ + "/list=" + ",".join(f"CON_SO_{c}" for c in batch)
                r = self._get(url, headers=SINA_HEADERS_SO, retries=1)
                for k, v in _parse_sina_var(r.text).items():
                    result[k.replace("CON_SO_", "")] = v
        except Exception as e:
            log.warning("Greeks 获取失败(降级为无 Greeks): %s", e)
        return result

    # ------------------------------------------------------------------
    def _fetch_spot(self) -> tuple:
        px = self.exchange
        r = self._get(SINA_HQ + f"/list={px}{self.underlying}", headers=SINA_HEADERS_OP)
        fields = _parse_sina_var(r.text).get(f"{px}{self.underlying}", [])
        if not fields or len(fields) < 32:
            raise RuntimeError(f"标的价格解析失败: {r.text[:120]}")
        try:
            price = float(fields[3])
        except (TypeError, ValueError):
            raise RuntimeError(f"标的价格字段异常: {fields[:5]}")
        spot_time = f"{fields[30]} {fields[31]}" if len(fields) > 31 else ""
        return price, spot_time

    # ------------------------------------------------------------------
    def _assemble(self, quotes: Dict[str, list], greeks: Dict[str, list], spot_time: str) -> List[Contract]:
        fetched = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        contracts: List[Contract] = []
        for code, f in quotes.items():
            if not f or len(f) < 48:
                continue
            name = f[37]
            try:
                strike = float(f[7])
                bid = float(f[22])   # 申买价一
                ask = float(f[20])   # 申卖价一
                bid_vol = _int(f[23])
                ask_vol = _int(f[21])
                last = _float(f[2])
                volume = _int(f[41])
                oi = _int(f[5])
                quote_time = f[32]
                expiry = f[46] if len(f) > 46 else ""
                days = _int(f[47]) if len(f) > 47 else 0
                mkt_flag = f[43] if len(f) > 43 else ""
            except (TypeError, ValueError):
                continue
            adjusted = is_adjusted_by_name(name) or (mkt_flag.upper() == "A")
            g = greeks.get(code, [])
            g_map = {"iv": None, "delta": None, "gamma": None, "theta": None, "vega": None,
                     "trade_code": "", "g_mkt_flag": ""}
            if g and len(g) >= 17:
                g_map = {
                    "iv": _float(g[9]), "delta": _float(g[5]), "gamma": _float(g[6]),
                    "theta": _float(g[7]), "vega": _float(g[8]),
                    "trade_code": g[12], "g_mkt_flag": g[16],
                }
                if not adjusted:
                    adjusted = g_map["g_mkt_flag"].upper() == "A"
            trade_code = g_map["trade_code"] or f""
            c = Contract(
                code=code, name=name, trade_code=trade_code, cp="P",
                strike=strike, expiry_date=expiry, days_to_expiry=days,
                is_adjusted=adjusted, bid=bid, ask=ask, bid_vol=bid_vol,
                ask_vol=ask_vol, last=last, volume=volume, open_interest=oi,
                iv=g_map["iv"], delta=g_map["delta"], gamma=g_map["gamma"],
                theta=g_map["theta"], vega=g_map["vega"],
                quote_time=quote_time, fetched_at=fetched,
            )
            validate_contract(c)
            contracts.append(c)
        log.info("解析有效合约 %d 个", len(contracts))
        return contracts

    # ------------------------------------------------------------------
    def _is_fresh(self, now: dt.datetime, spot_time: str, contracts: List[Contract]) -> bool:
        """依据行情时间与抓取时间判断数据是否新鲜"""
        # 盘口有任意一条在陈旧窗口内则视为新鲜(批量行情时间基本一致)
        for c in contracts:
            if not c.quote_time:
                continue
            try:
                qt = dt.datetime.strptime(c.quote_time, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            if qt > now or (now - qt).total_seconds() <= self.stale_seconds:
                return True
        return False

    # ------------------------------------------------------------------
    def status(self) -> dict:
        return {
            "name": self.name,
            "ok": self._consecutive_failures == 0 and self._last_ok_at is not None,
            "last_ok_at": self._last_ok_at.strftime("%Y-%m-%d %H:%M:%S") if self._last_ok_at else None,
            "consecutive_failures": self._consecutive_failures,
            "stale_seconds": self.stale_seconds,
        }

    def close(self) -> None:
        try:
            self._session.close()
        except Exception:
            pass


# ----------------------------------------------------------------------
# 解析工具
# ----------------------------------------------------------------------

def _parse_sina_var(text: str) -> Dict[str, list]:
    """解析 hq.sinajs.cn 返回: var hq_str_XXX="a,b,c";  -> {XXX: [a,b,c]}"""
    result: Dict[str, list] = {}
    for line in text.split(";"):
        line = line.strip()
        if not line:
            continue
        idx = line.find("=")
        if idx < 0:
            continue
        key = line[:idx].replace("var hq_str_", "").strip()
        val = line[idx + 1:].strip().strip('"').strip()
        result[key] = val.split(",") if val else []
    return result


def _float(v) -> float:
    try:
        s = str(v).strip()
        return float(s) if s else 0.0
    except (TypeError, ValueError):
        return 0.0


def _int(v) -> int:
    try:
        s = str(v).strip()
        return int(float(s)) if s else 0
    except (TypeError, ValueError):
        return 0


def cfg_contracts() -> dict:
    """读取合约相关配置(延迟加载避免循环依赖)"""
    from util import load_config
    return load_config().get("contracts", {})
