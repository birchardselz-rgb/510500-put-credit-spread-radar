# -*- coding: utf-8 -*-
"""
core/alerts.py
报警机制:
- 同一组合必须连续 N 次(默认 3)扫描满足条件才触发正式报警, 避免瞬时异常盘口假信号
- 报警后进入冷却期; 除非评分明显提升 / 净收明显改善 / 排名重大变化, 否则不重复刷屏
- 数据陈旧或数据源断开时停止报警
"""
from __future__ import annotations

import datetime as dt
import threading
from typing import Dict, List, Optional

from core.scoring import qualify_for_alert


class AlertManager:
    def __init__(self, cfg: dict):
        a = cfg.get("alerts", {})
        self.confirm_required = int(a.get("confirm_required", 3))
        self.cooldown_seconds = float(a.get("cooldown_seconds", 300))
        self.re_alert_score_delta = float(a.get("re_alert_score_delta", 0.5))
        self.re_alert_credit_delta = float(a.get("re_alert_credit_delta", 0.010))
        self.console_enabled = bool(a.get("console_enabled", True))
        self.sound_enabled = bool(a.get("sound_enabled", True))
        self._lock = threading.Lock()
        # key = spread 组合唯一标识(卖出代码:买入代码)
        self._state: Dict[str, dict] = {}

    # ------------------------------------------------------------------
    def _key(self, spread) -> str:
        return f"{spread.sell.code}:{spread.buy.code}"

    def reset(self) -> None:
        with self._lock:
            self._state.clear()

    def snapshot(self) -> List[dict]:
        """供看板/日志展示的当前报警状态"""
        with self._lock:
            return [
                {"key": k, **v}
                for k, v in sorted(
                    self._state.items(),
                    key=lambda kv: kv[1].get("last_score", 0.0),
                    reverse=True,
                )
            ]

    # ------------------------------------------------------------------
    def update(
        self,
        spread,
        score_result: dict,
        cfg: dict,
        data_fresh: bool = True,
        now: Optional[dt.datetime] = None,
    ) -> Optional[dict]:
        """每轮扫描调用一次。返回报警事件 dict(触发时)或 None。

        alert event:
          {key, label, consec, score, credit, spread_dict, spot, time, reason}
        """
        now = now or dt.datetime.now()
        key = self._key(spread)

        qualifies = data_fresh and qualify_for_alert(spread, score_result, cfg)

        with self._lock:
            st = self._state.setdefault(
                key,
                {
                    "consec": 0,
                    "last_alert_ts": None,
                    "last_alert_score": -1.0,
                    "last_alert_credit": -1.0,
                    "last_score": -1.0,
                    "last_credit": -1.0,
                    "alerts": 0,
                },
            )

            if not qualifies:
                st["consec"] = 0
                st["last_score"] = score_result.get("score", 0.0)
                st["last_credit"] = spread.credit
                return None

            st["consec"] += 1
            st["last_score"] = score_result.get("score", 0.0)
            st["last_credit"] = spread.credit

            if st["consec"] < self.confirm_required:
                return None  # 连续次数不足

            # 连续次数已达标 -> 检查是否允许报警
            can_alert = False
            reason = ""
            if st["last_alert_ts"] is None:
                can_alert = True
                reason = "首次满足"
            else:
                elapsed = (now - st["last_alert_ts"]).total_seconds()
                score_up = st["last_score"] - st["last_alert_score"]
                credit_up = st["last_credit"] - st["last_alert_credit"]
                if elapsed >= self.cooldown_seconds:
                    can_alert = True
                    reason = "冷却期已过"
                elif score_up >= self.re_alert_score_delta:
                    can_alert = True
                    reason = f"评分提升{score_up:+.2f}"
                elif credit_up >= self.re_alert_credit_delta:
                    can_alert = True
                    reason = f"净收提升{credit_up:+.4f}"

            if not can_alert:
                return None

            # 触发正式报警
            st["last_alert_ts"] = now
            st["last_alert_score"] = st["last_score"]
            st["last_alert_credit"] = st["last_credit"]
            st["consec"] = 0  # 触发后重置连续计数
            st["alerts"] += 1

            event = {
                "key": key,
                "label": spread.label,
                "consec": self.confirm_required,
                "score": st["last_score"],
                "credit": spread.credit,
                "spread": spread.to_dict(),
                "spot": spread.spot,
                "time": now.strftime("%Y-%m-%d %H:%M:%S"),
                "reason": reason,
                "total_alerts": st["alerts"],
            }
            if self.sound_enabled:
                self._beep()
            if self.console_enabled:
                self._console(event)
            return event

    # ------------------------------------------------------------------
    @staticmethod
    def _beep() -> None:
        try:
            import winsound

            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception:
            try:
                print("\a", end="", flush=True)
            except Exception:
                pass

    @staticmethod
    def _console(event: dict) -> None:
        sp = event["spread"]
        print(
            "\n" + "=" * 78 + f"\n[报警] {event['time']}  510500={event['spot']:.4f}  "
            f"{event['label']}  (原因:{event['reason']})\n"
            f"  净收={event['credit']:.4f}  最大盈利={sp['max_profit']:.0f}元  最大亏损={sp['max_loss']:.0f}元\n"
            f"  BE={sp['breakeven']:.4f}  安全垫={sp['safety_margin']*100:.2f}%  "
            f"收益/风险={sp['reward_risk']*100:.1f}%\n"
            f"  卖出腿 {sp['sell_name']} Bid={sp.get('sell_bid','')}  "
            f"Delta={sp.get('sell_delta','')} IV={sp.get('sell_iv','')}\n"
            f"  评分={event['score']:.2f}  连续满足次数={event['consec']}\n"
            + "=" * 78,
            flush=True,
        )
