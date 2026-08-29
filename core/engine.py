# -*- coding: utf-8 -*-
"""
core/engine.py
扫描引擎: 编排 数据源 -> 合约过滤 -> 价差生成 -> 指标计算 -> 评分 -> 报警 -> 入库。

支持多标的(510500 / 588080 等): 每个标的独立采集/计算/报警, 结果按 underlying 区分。

- 单标的 run_single(code): 返回该标的 ScanResult
- 全量 run_once(): 返回 {underlying: ScanResult}
- 主循环 run_forever(): 按扫描间隔循环; 关键异常不会导致主循环永久退出
- 数据陈旧或数据源断开时停止报警
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Dict, List, Optional

from core.alerts import AlertManager
from core.contracts import Contract
from core.scoring import score_spread
from core.spreads import Spread, account_risk, generate_put_credit_spreads, suggested_lots
from data_sources.base import MarketDataSource, MarketSnapshot
from storage.database import Database
from util import load_config

log = logging.getLogger("scanner.engine")


class ScanResult:
    """单标的一轮扫描的完整结果(供看板/日志/入库使用)"""
    def __init__(self, snapshot: MarketSnapshot, underlying: str = ""):
        self.snapshot = snapshot
        self.underlying = underlying or snapshot.underlying
        self.spreads: List[Spread] = []
        self.scored: List[dict] = []          # 带评分字段的 dict(仅有效组合)
        self.alerts: List[dict] = []
        self.scan_id: Optional[int] = None
        self.error: Optional[str] = None
        self.started = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def top(self, n: int = 10) -> List[dict]:
        return self.scored[:n]


def _enabled_underlyings(cfg: dict) -> Dict[str, dict]:
    """返回 {code: underlying_cfg} (仅 enabled 的标的)"""
    u = cfg.get("underlyings") or {}
    result = {}
    for code, uc in u.items():
        if not uc.get("enabled", True):
            continue
        uc = dict(uc)
        uc["code"] = str(code)
        result[str(code)] = uc
    return result


class ScanEngine:
    def __init__(self, cfg: Optional[dict] = None, source: Optional[MarketDataSource] = None):
        self.cfg = cfg or load_config()
        self.db = Database(self.cfg.get("storage", {}).get("database", "data/scanner.db"))
        self.alerts = AlertManager(self.cfg)
        self.underlyings = _enabled_underlyings(self.cfg)
        if source is not None:
            # 单源模式(测试用): 仅构造一个标的的源
            code = getattr(source, "underlying", "") or next(iter(self.underlyings), "510500")
            uc = self.underlyings.get(code) or {"code": code, "name": code}
            self.underlyings = {code: uc}
            self.sources: Dict[str, MarketDataSource] = {code: source}
        else:
            self.sources = {
                code: _create_source(self.cfg, uc)
                for code, uc in self.underlyings.items()
            }

    def close(self) -> None:
        for s in self.sources.values():
            try:
                s.close()
            except Exception:
                pass
        self.db.close()

    # ------------------------------------------------------------------
    def run_single(self, underlying_code: str) -> ScanResult:
        """扫描单个标的"""
        source = self.sources.get(underlying_code)
        if source is None:
            raise KeyError(f"未配置标的: {underlying_code}")
        uc = self.underlyings.get(underlying_code, {})
        result = ScanResult(source.fetch_snapshot(), underlying=underlying_code)
        snap = result.snapshot

        if snap.error or not snap.contracts or not snap.spot or snap.spot <= 0:
            result.error = snap.error or "无合约数据"
            log.warning("%s 本轮无数据: %s", underlying_code, result.error)
            # 不保存空扫描行(spot<=0 / 无合约 / 数据源断开), 避免污染"最新记录"
            # 导致看板短暂显示 0.0000 / 空候选; 保留上一轮有效数据
            return result

        # 1) 有效认沽
        puts = [c for c in snap.contracts if c.valid and not c.is_adjusted]
        log.info("%s 有效认沽合约 %d 个, 标的 %.4f, 新鲜=%s",
                 underlying_code, len(puts), snap.spot, snap.fresh)

        # 2) 生成 Put 信用价差(每标的独立宽度)
        widths = [float(w) for w in uc.get("strike_widths", [0.25, 0.50])]
        multiplier = int(uc.get("multiplier", 10000))
        spreads = generate_put_credit_spreads(
            puts, widths, snap.spot, multiplier=multiplier, underlying=underlying_code,
        )

        # 3) 指标 + 评分
        scoring_cfg = self.cfg.get("scoring", {})
        strategy = self.cfg.get("strategy", {})
        account = self.cfg.get("account", {})
        capital = float(account.get("capital", 500000))
        risk_budget_pct = float(account.get("max_risk_per_batch_pct", 0.02))
        lots_cap = int(account.get("suggested_lots_cap", 100))
        min_score_pass = float(scoring_cfg.get("min_score_pass", 5.0))
        # 每标的可覆盖评分锚点(如 588080 权利金量级小)
        anchors = _default_anchors(scoring_cfg)
        if uc.get("credit_anchor"):
            anchors["credit"] = uc["credit_anchor"]
        # 每标的可覆盖报警最低净收
        alert_cfg = dict(self.cfg)
        alert_cfg["alerts"] = dict(self.cfg.get("alerts", {}))
        if uc.get("alert_min_credit"):
            alert_cfg["alerts"]["min_credit_to_alert"] = uc["alert_min_credit"]

        scored = []
        for sp in spreads:
            if not sp.valid:
                continue
            score_map = score_spread(
                sp,
                weights=scoring_cfg.get("weights", {}),
                anchors=anchors,
                min_safety=float(strategy.get("min_safety_margin", 0.02)),
                delta_min=float(strategy.get("sell_delta_min_abs", 0.15)),
                delta_max=float(strategy.get("sell_delta_max_abs", 0.35)),
                iv_good_min=float(scoring_cfg.get("iv_good_min", 0.15)),
                iv_good_max=float(scoring_cfg.get("iv_good_max", 0.45)),
                max_slip_ratio=float(strategy.get("max_leg_bid_ask_ratio", 0.50)),
                min_vol=int(strategy.get("min_volume", 20)),
                min_oi=int(strategy.get("min_open_interest", 100)),
                pop_min_hard=float(scoring_cfg.get("pop_min_hard", 0.60)),
                pop_min_soft=float(scoring_cfg.get("pop_min_soft", 0.70)),
            )
            d = sp.to_dict()
            d.update(score_map)
            d["suggested_lots"] = suggested_lots(sp, capital, risk_budget_pct, lots_cap)
            risk_groups = {}
            for n in (1, 3, 5, 10):
                risk_groups[f"lots{n}"] = account_risk(sp, capital, n)
            d["risk_groups"] = risk_groups
            d["spot"] = snap.spot
            d["underlying"] = underlying_code
            d["underlying_name"] = snap.underlying_name
            d["consec_hits"] = 0
            d["alert_fired"] = False
            if score_map["score"] >= min_score_pass:
                scored.append(d)

        # 4) 排序(评分降序)
        scored.sort(key=lambda x: (x["score"], x.get("ev") or 0.0), reverse=True)

        # 5) 报警(仅针对达标组合)
        for d in scored:
            sp = _dict_to_spread(d)
            event = self.alerts.update(sp, d, alert_cfg, data_fresh=snap.fresh)
            if event:
                d["consec_hits"] = self.cfg["alerts"].get("confirm_required", 3)
                d["alert_fired"] = True
                result.alerts.append(event)

        # 记录连续满足次数(未触发报警的观察中组合)
        for st in self.alerts.snapshot():
            for d in scored:
                if f"{d['sell_code']}:{d['buy_code']}" == st["key"]:
                    d["consec_hits"] = st.get("consec", 0)
                    d["last_alert_ts"] = st.get("last_alert_ts")

        result.spreads = spreads
        result.scored = scored
        self._save(result)
        return result

    # ------------------------------------------------------------------
    def run_once(self) -> Dict[str, ScanResult]:
        """扫描全部启用的标的"""
        return {code: self.run_single(code) for code in self.underlyings}

    # ------------------------------------------------------------------
    def _save(self, result: ScanResult) -> None:
        try:
            snap = result.snapshot
            result.scan_id = self.db.save_scan(
                scan_time=result.started,
                source=snap.source,
                spot=snap.spot,
                spot_time=snap.spot_time or "",
                data_fresh=snap.fresh,
                months=snap.target_months,
                contracts=snap.contracts,
                spreads=result.scored,
                n_alerts=len(result.alerts),
                underlying=result.underlying,
                underlying_name=snap.underlying_name,
            )
        except Exception as e:
            log.error("入库失败: %s", e)

    # ------------------------------------------------------------------
    def run_forever(self, interval: Optional[float] = None, max_rounds: Optional[int] = None) -> None:
        """主扫描循环。单轮异常不会导致循环退出。

        :param interval: 覆盖配置的扫描间隔(秒)
        :param max_rounds: 限制轮数(测试/演示用, None=无限)
        """
        interval = interval or float(self.cfg["data_source"].get("scan_interval_seconds", 5))
        targets = list(self.underlyings.keys())
        log.info("开始扫描循环, 数据源=%s, 标的=%s, 间隔=%.1fs",
                 self._source_name(), targets, interval)
        print(f"[启动] 数据源={self._source_name()} 标的={targets} 扫描间隔={interval}s "
              f"数据陈旧窗口={self.cfg['data_source'].get('quote_stale_seconds',30)}s", flush=True)
        rounds = 0
        while max_rounds is None or rounds < max_rounds:
            t0 = time.time()
            try:
                results = self.run_once()
                for code in targets:
                    self._print_round(results.get(code))
            except Exception as e:
                log.exception("本轮扫描异常(循环继续): %s", e)
            rounds += 1
            elapsed = time.time() - t0
            sleep = max(0.1, interval - elapsed)
            time.sleep(sleep)

    # ------------------------------------------------------------------
    def _source_name(self) -> str:
        names = {s.name for s in self.sources.values()}
        return "+".join(sorted(names)) or "?"

    # ------------------------------------------------------------------
    @staticmethod
    def _print_round(result: Optional[ScanResult]) -> None:
        if result is None:
            return
        snap = result.snapshot
        tag = f"{result.underlying}({snap.underlying_name})"
        if snap.error:
            print(f"[{result.started}] {tag} 行情异常: {snap.error}", flush=True)
            return
        status = "新鲜" if snap.fresh else "行情陈旧(停止报警)"
        print(
            f"[{result.started}] {tag} 现货={snap.spot:.4f} 源={snap.source} {status} "
            f"合约={snap.contract_count()} 候选={len(result.scored)} 报警={len(result.alerts)}",
            flush=True,
        )
        for d in result.scored[:10]:
            _pop = d.get('pop')
            _pop_s = f"{_pop*100:5.1f}%" if _pop is not None else '   -  '
            _ev_s = f"{d.get('ev'):6.0f}" if d.get('ev') is not None else '    - '
            print(
                f"  {d['label']:>12s} 净收={d['credit']:.4f} 盈利={d['max_profit']:.0f} "
                f"亏损={d['max_loss']:.0f} BE={d['breakeven']:.4f} "
                f"安全垫={d['safety_margin']*100:5.2f}% RR={d['reward_risk']*100:5.1f}% "
                f"POP={_pop_s} EV={_ev_s} "
                f"Delta={d.get('sell_delta') if d.get('sell_delta') is not None else '-':>7} "
                f"评分={d['score']:5.2f} {d['tier']}",
                flush=True,
            )


# ----------------------------------------------------------------------
# 工具
# ----------------------------------------------------------------------

def _create_source(cfg: dict, underlying_cfg: dict) -> MarketDataSource:
    primary = cfg["data_source"].get("primary", "tonghuashun")
    if primary == "mock":
        from data_sources.mock import MockDataSource
        return MockDataSource(cfg, underlying_cfg)
    if primary == "tonghuashun":
        from data_sources.tonghuashun import TonghuashunDataSource
        return TonghuashunDataSource(cfg, underlying_cfg)
    if primary == "akshare_sina":
        from data_sources.akshare_sina import AkshareSinaDataSource
        return AkshareSinaDataSource(cfg, underlying_cfg)
    if primary == "eastmoney":
        from data_sources.eastmoney import EastmoneyDataSource
        return EastmoneyDataSource(cfg, underlying_cfg)
    if primary == "qmt":
        from data_sources.qmt import QmtDataSource
        return QmtDataSource(cfg)
    raise ValueError(f"未知数据源: {primary}")


def _default_anchors(scoring_cfg: dict) -> dict:
    """兼容旧配置: 若未在 scoring.anchors 定义, 从扁平键推导"""
    return {
        "credit": scoring_cfg.get("credit_anchor", {"a6": 0.085, "a8": 0.095, "a10": 0.105}),
        "safety": scoring_cfg.get("safety_anchor", {"a8": 0.02, "a10": 0.04}),
        "rr": scoring_cfg.get("rr_anchor", {"a8": 0.50, "a10": 0.80}),
    }


def _dict_to_spread(d: dict) -> Spread:
    """将评分 dict 还原为 Spread(供报警管理器使用)"""
    from core.contracts import Contract

    sell = Contract(
        code=d["sell_code"], name=d.get("sell_name", ""), trade_code="",
        cp="P", strike=d["sell_strike"], expiry_date="", days_to_expiry=0,
        is_adjusted=False, bid=0.0, ask=0.0,
        delta=d.get("sell_delta"), iv=d.get("sell_iv"),
    )
    buy = Contract(
        code=d["buy_code"], name=d.get("buy_name", ""), trade_code="",
        cp="P", strike=d["buy_strike"], expiry_date="", days_to_expiry=0,
        is_adjusted=False, bid=0.0, ask=0.0,
        delta=d.get("buy_delta"), iv=d.get("buy_iv"),
    )
    sp = Spread(sell=sell, buy=buy, width=d["width"], spot=d.get("spot", 0.0))
    sp.credit = d["credit"]
    sp.mid_credit = d.get("mid_credit", d["credit"])
    sp.max_profit = d["max_profit"]
    sp.max_loss = d["max_loss"]
    sp.breakeven = d["breakeven"]
    sp.safety_margin = d["safety_margin"]
    sp.reward_risk = d["reward_risk"]
    sp.total_slippage = d.get("total_slippage", 0.0)
    sp.underlying = d.get("underlying", "")
    return sp

