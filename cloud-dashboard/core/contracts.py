# -*- coding: utf-8 -*-
"""
core/contracts.py
合约识别与过滤模块：
- 解析新浪期权合约简称 / 上交所交易代码
- 识别标准 M 合约与除息后的 A 类调整合约
- 计算剩余到期天数、按窗口筛选到期月份
- Contract 数据类统一承载期权盘口与 Greeks
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import List, Optional

# ---------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------


@dataclass
class Contract:
    """单一期权合约的实时快照（认沽或认购）"""
    code: str                 # 8 位新浪内部合约代码, 如 10012280
    name: str                 # 合约简称, 如 500ETF沽9月6250
    trade_code: str           # 上交所交易代码, 如 510500P2609M06250
    cp: str                   # 'C' 认购 / 'P' 认沽
    strike: float             # 行权价
    expiry_date: str          # 到期日 YYYY-MM-DD
    days_to_expiry: int       # 剩余天数
    is_adjusted: bool         # True=除息调整 A 类合约
    # 盘口
    bid: float = 0.0          # Bid1 买一价
    ask: float = 0.0          # Ask1 卖一价
    bid_vol: int = 0          # Bid1 买一量
    ask_vol: int = 0          # Ask1 卖一量
    last: float = 0.0         # 最新价
    volume: int = 0           # 成交量
    open_interest: int = 0    # 持仓量
    # Greeks（免费源可取得时填充）
    iv: Optional[float] = None
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None
    # 数据时间
    quote_time: Optional[str] = None   # 交易所行情时间
    fetched_at: Optional[str] = None   # 本地抓取时间
    # 数据质量
    valid: bool = True        # False 表示数据缺失/异常, 不可用于交易计算
    invalid_reason: str = ""

    @property
    def spread_width(self) -> float:
        """盘口宽度(价差)"""
        return max(0.0, self.ask - self.bid)

    def to_dict(self) -> dict:
        return {
            "code": self.code, "name": self.name, "trade_code": self.trade_code,
            "cp": self.cp, "strike": self.strike, "expiry_date": self.expiry_date,
            "days_to_expiry": self.days_to_expiry, "is_adjusted": self.is_adjusted,
            "bid": self.bid, "ask": self.ask, "bid_vol": self.bid_vol,
            "ask_vol": self.ask_vol, "last": self.last,
            "volume": self.volume, "open_interest": self.open_interest,
            "iv": self.iv, "delta": self.delta, "gamma": self.gamma,
            "theta": self.theta, "vega": self.vega,
            "quote_time": self.quote_time, "fetched_at": self.fetched_at,
            "valid": self.valid, "invalid_reason": self.invalid_reason,
        }


# ---------------------------------------------------------------
# 解析函数
# ---------------------------------------------------------------

_ADJUSTED_NAME = re.compile(r"(?:购|沽)(\d+月)(\d+(?:\.\d+)?)([A-Z]*)\s*$")
_TRADE_CODE = re.compile(
    r"^(?P<underlying>\d{6})(?P<cp>[CP])(?P<yymm>\d{4})(?P<flag>[MA])(?P<strike>\d{4,8})$"
)


def is_adjusted_by_name(name: str) -> bool:
    """按合约简称判断是否 A 类调整合约。

    标准合约简称形如: 500ETF沽9月6250
    调整合约简称形如: 500ETF沽9月6385A  (行权价后带 A)
    """
    if not name:
        return False
    m = _ADJUSTED_NAME.search(name.strip())
    if not m:
        # 无法匹配时保守按“名称含 A”判断
        return "A" in name.upper()
    flag = (m.group(3) or "").upper()
    return "A" in flag


def parse_cp_from_name(name: str) -> str:
    """从合约简称判断 认购/认沽"""
    return "P" if ("沽" in name) else ("C" if ("购" in name) else "?")


def parse_trade_code(trade_code: str) -> Optional[dict]:
    """解析上交所交易代码, 如 510500P2609M06250 -> {'cp':'P','yymm':'2609','flag':'M','strike':6.25}"""
    if not trade_code:
        return None
    m = _TRADE_CODE.match(trade_code.strip())
    if not m:
        return None
    # 上交所交易代码行权价编码为"千分之X元", 如 06250 -> 6.250
    strike = int(m.group("strike")) / 1000.0
    return {
        "underlying": m.group("underlying"),
        "cp": m.group("cp"),
        "yymm": m.group("yymm"),
        "flag": m.group("flag"),
        "strike": strike,
        "is_adjusted": m.group("flag").upper() == "A",
    }


def parse_expiry_from_name(name: str) -> Optional[dt.date]:
    """从合约简称提取到期月份(用于月份标签解析, 返回值不含年可能不完整, 慎用)"""
    m = re.search(r"([购沽])(\d+)月", name)
    if not m:
        return None
    month = int(m.group(2))
    return dt.date(year=2000, month=month, day=1)  # 仅占位


# ---------------------------------------------------------------
# 到期日/月份筛选
# ---------------------------------------------------------------


def parse_date(s: str) -> Optional[dt.date]:
    """解析 YYYY-MM-DD / YYYYMMDD / YYYY-MM"""
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y-%m", "%Y%m"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def days_to_expiry(expiry: str, today: Optional[dt.date] = None) -> Optional[int]:
    """计算剩余到期天数(自然日)。expiry: YYYY-MM-DD"""
    ed = parse_date(expiry)
    if ed is None:
        return None
    today = today or dt.date.today()
    return (ed - today).days


def filter_expiry_months(
    months: List[str],
    days_min: int,
    days_max: int,
    today: Optional[dt.date] = None,
) -> List[str]:
    """从合约到期月份列表(如 ['202609','202610',...])中筛选剩余天数在 [days_min, days_max] 的月份。

    到期日按当月第 4 个星期三近似(场内期权行权日惯例), 实际以数据源返回的到期日为准。
    """
    today = today or dt.date.today()
    result = []
    for m in months:
        m = m.strip()
        if len(m) != 6 or not m.isdigit():
            continue
        year, month = int(m[:4]), int(m[4:])
        try:
            expiry = fourth_wednesday(year, month)
        except ValueError:
            continue
        d = (expiry - today).days
        if days_min <= d <= days_max:
            result.append(m)
    return sorted(result)


def fourth_wednesday(year: int, month: int) -> dt.date:
    """当月第 4 个星期三(用于近似计算剩余天数)"""
    first = dt.date(year, month, 1)
    # 1 号是星期几, 计算第一个星期三
    offset = (2 - first.weekday()) % 7  # Wednesday=2
    first_wed = first + dt.timedelta(days=offset)
    return first_wed + dt.timedelta(weeks=3)


def pick_next_expiry_months(
    months: List[str],
    count: int,
    today: Optional[dt.date] = None,
) -> List[str]:
    """从可用到期月份(YYYYMM)中选接下来 count 个仍在未来(到期日>今天)的月份。

    按剩余天数升序取前 count 个; count<=0 返回空。
    用于"扫描接下来 N 个到期月份"(如 9 月+10 月), 不依赖天数窗口。
    """
    today = today or dt.date.today()
    future = []
    for m in months:
        m = m.strip()
        if len(m) != 6 or not m.isdigit():
            continue
        year, month = int(m[:4]), int(m[4:])
        try:
            expiry = fourth_wednesday(year, month)
        except ValueError:
            continue
        remain = (expiry - today).days
        if remain > 0:
            future.append((m, remain))
    future.sort(key=lambda x: x[1])
    return [m for m, _ in future[:max(0, int(count))]]


def select_target_months(
    months: List[str],
    days_min: int,
    days_max: int,
    preferred: Optional[str] = None,
    today: Optional[dt.date] = None,
) -> List[str]:
    """选择扫描的目标到期月份。

    - 若用户指定 preferred 且在其范围内, 优先只扫该月份
    - 否则返回所有落在剩余天数窗口内的月份
    """
    today = today or dt.date.today()
    in_window = filter_expiry_months(months, days_min, days_max, today)
    if preferred:
        if preferred in in_window:
            return [preferred]
        # 指定月份不在窗口内: 明确忽略(避免误扫远期合约)
        return []
    return in_window


# ---------------------------------------------------------------
# 数据质量校验
# ---------------------------------------------------------------


def validate_contract(c: Contract) -> Contract:
    """校验合约数据是否可用于价差计算。

    过滤规则:
    - A 类调整合约不可用
    - Bid/Ask 任一为 0 或为负 => 不可成交
    - 盘口/行权价/到期日缺失 => 不可用
    """
    reasons = []
    if c.is_adjusted:
        reasons.append("A类调整合约")
    if c.bid <= 0:
        reasons.append("Bid1<=0")
    if c.ask <= 0:
        reasons.append("Ask1<=0")
    if c.ask < c.bid:
        reasons.append("Ask<Bid 盘口异常")
    if c.strike <= 0:
        reasons.append("行权价异常")
    if not c.expiry_date:
        reasons.append("到期日缺失")
    if reasons:
        c.valid = False
        c.invalid_reason = "|".join(reasons)
    else:
        c.valid = True
        c.invalid_reason = ""
    return c


def mark_stale(c: Contract, now: dt.datetime, max_age_seconds: int) -> Contract:
    """依据行情时间/抓取时间标记陈旧数据"""
    if c.quote_time:
        qt = parse_datetime(c.quote_time)
        if qt and (now - qt).total_seconds() > max_age_seconds:
            c.valid = False
            c.invalid_reason = (c.invalid_reason + "|行情陈旧").strip("|")
    return c


def parse_datetime(s: str) -> Optional[dt.datetime]:
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None
