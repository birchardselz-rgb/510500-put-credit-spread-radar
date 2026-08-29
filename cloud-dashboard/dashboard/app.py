# -*- coding: utf-8 -*-
"""
dashboard/app.py
510500 + 588080 Put 信用价差实时看板(Streamlit 本地网页)。

启动:  python run_dashboard.py
数据:  读取 SQLite 最新扫描结果(需先运行 run_scanner.py, 或点击"实时获取数据")
功能:
  - 「实时获取数据」按钮: 点击后主动扫描 510500 与 588080 并更新看板
  - Top10 实时排行榜 + 过滤(标的/到期月份/宽度/最低安全垫/最低净收/最低评分)
  - 最近报警 / 扫描历史
"""
from __future__ import annotations

import os
import sys

import pandas as pd
import streamlit as st

# 保证以项目根为工作目录, 便于 import 与定位数据库
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

from storage.database import Database  # noqa: E402
from util import load_config  # noqa: E402

CFG = load_config()
DB = Database(CFG.get("storage", {}).get("database", "data/scanner.db"))

st.set_page_config(
    page_title="Put 信用价差实时扫描器 V1.3 (多标的宽基 ETF)",
    page_icon="📡",
    layout="wide",
)

TIER_COLOR = {
    "强机会": "#e74c3c",
    "优质机会": "#f39c12",
    "观察": "#3498db",
    "跳过": "#95a5a6",
}

UNDERLYING_NAMES = {k: v.get("name", k) for k, v in (CFG.get("underlyings") or {}).items()}


def _fmt(x, nd=4):
    if x is None or (isinstance(x, float) and x != x):
        return "-"
    try:
        return f"{float(x):.{nd}f}"
    except (TypeError, ValueError):
        return "-"


def _pct(x, nd=2):
    if x is None:
        return "-"
    try:
        return f"{float(x) * 100:.{nd}f}%"
    except (TypeError, ValueError):
        return "-"


def _trading_session() -> str:
    """按 A 股期权交易时段判断: 工作日 09:30-11:30 / 13:00-15:00 => 'trading', 否则 'closed'"""
    import datetime as dt
    now = dt.datetime.now()
    if now.weekday() >= 5:
        return "closed"
    t = now.hour * 60 + now.minute
    if (9 * 60 + 30) <= t <= (11 * 60 + 30) or (13 * 60) <= t <= (15 * 60):
        return "trading"
    return "closed"


def load_latest():
    """读取每个标的最新一次扫描及其候选价差"""
    scans = {}   # underlying -> scan dict
    spreads = []  # list of spread rows (带 scan_id, underlying)
    for code in (CFG.get("underlyings") or {}):
        sc = DB.latest_valid_scan_for(code)
        if sc:
            scans[code] = sc
            spreads.extend(DB.spreads_by_scan(sc["id"], min_score=0.0))
    alerts = DB.recent_alerts(limit=30)
    return scans, spreads, alerts


def run_scan(cfg: Optional[dict] = None) -> str:
    """主动扫描全部启用的标的并入库(纯逻辑, 无 st 依赖, 可单测)。

    返回 '' 表示成功, 否则返回错误信息。对应「实时获取数据」按钮点击后的实际动作。
    :param cfg: 可注入配置(测试时传 mock 模式), 默认用全局 CFG
    """
    from core.engine import ScanEngine
    # 每次点击重读 config.yaml, 修改阈值/到期月数量后无需重启看板
    cfg = cfg if cfg is not None else load_config()
    try:
        engine = ScanEngine(cfg)
        try:
            engine.run_once()
        finally:
            engine.close()
        return ""
    except Exception as e:
        return str(e)


def do_scan():
    """点击"实时获取数据"时在主脚本体内调用: 扫描 + 展示反馈 + 刷新"""
    with st.spinner("正在主动扫描全部标的 ..."):
        err = run_scan()
    if err:
        st.error(f"扫描失败: {err}")
        return False
    st.success("扫描完成, 数据已更新")
    return True


def render():
    st.title("📡 Put 信用价差实时扫描器 V1.3")
    st.caption("宽基 ETF 期权(上交所+深交所) ｜ 新浪期权主源 ｜ 只扫描与提醒，绝不自动下单")

    # ---------- 实时获取数据按钮(点击后在主脚本体内主动扫描) ----------
    col_btn, _ = st.columns([2, 8])
    with col_btn:
        clicked = st.button("🔄 实时获取数据", type="primary", width="stretch")
    if clicked:
        if do_scan():
            st.rerun()

    scans, spreads, alerts = load_latest()

    if not scans:
        st.warning(
            "尚未有扫描数据。点击上方「🔄 实时获取数据」立即扫描全部标的；"
            "或先运行 `python run_scanner.py`；无网络环境可用 `python run_scanner.py --mode mock` 体验。"
        )
        return

    # ---------- 标的概览表(每行一个标的, 状态上色) ----------
    ov_rows = []
    for code, sc in scans.items():
        ov_rows.append({
            "标的": UNDERLYING_NAMES.get(code, code),
            "代码": code,
            "现价": _fmt(sc["spot"], 4),
            "数据时间": sc.get("spot_time") or "—",
            "状态": "新鲜" if sc["data_fresh"] else "行情陈旧",
            "合约数": sc.get("n_contracts", "-"),
            "候选数": sc.get("n_spreads", "-"),
            "报警数": sc.get("n_alerts", "-"),
        })
    ov = pd.DataFrame(ov_rows)

    def _color_state(v):
        if v == "新鲜":
            return "color: #1e8e3e; font-weight: 700"
        if v == "行情陈旧":
            return "color: #b45309; font-weight: 600"
        return ""

    st.dataframe(ov.style.map(_color_state, subset=["状态"]),
                 width="stretch", hide_index=True)

    # ---------- 交易时段提示 ----------
    sess = _trading_session()
    stale_any = any(not sc["data_fresh"] for sc in scans.values())
    if sess == "trading":
        if stale_any:
            st.error("⚠️ 存在行情陈旧标的：数据超过设定秒数未更新，已停止该标的报警。请检查网络或数据源。")
    else:
        st.info("🕐 当前为交易时段外（A 股 09:30-11:30 / 13:00-15:00，周一至周五）。"
                "现价显示收盘数据（'陈旧'属正常），开盘后点击「实时获取数据」自动恢复实时与报警。")

    if not spreads:
        st.info("本轮无候选价差（可能是盘口/合约不足或全部被过滤）。")
        return

    df = pd.DataFrame(spreads)
    df["label_display"] = df.apply(
        lambda r: f"{float(r['sell_strike']):.2f}/{float(r['buy_strike']):.2f}P", axis=1
    )
    df["underlying_name"] = df["underlying"].map(UNDERLYING_NAMES).fillna(df["underlying"])

    # ---------- 过滤条件 ----------
    st.sidebar.header("过滤条件")
    un_names = sorted(df["underlying_name"].unique())
    sel_under = st.sidebar.selectbox("标的", ["全部"] + list(un_names))
    months = sorted(set(str(m) for m in df["expire_month"].dropna() if m))
    sel_month = st.sidebar.selectbox("到期月份", ["全部"] + months)
    widths = sorted(set(float(w) for w in df["width"].dropna()))
    sel_width = st.sidebar.selectbox("执行价宽度", ["全部"] + [f"{w:.2f}" for w in widths])
    min_safety = st.sidebar.slider("最低安全垫", 0.0, 0.10, 0.00, 0.005, format="%.3f")
    min_credit = st.sidebar.slider("最低净收", 0.0, 0.20, 0.000, 0.005, format="%.3f")
    min_score = st.sidebar.slider("最低评分", 0.0, 10.0, 5.0, 0.1)

    view = df.copy()
    if sel_under != "全部":
        view = view[view["underlying_name"] == sel_under]
    if sel_month != "全部":
        view = view[view["expire_month"] == sel_month]
    if sel_width != "全部":
        view = view[view["width"].astype(float) == float(sel_width)]
    view = view[view["safety_margin"].astype(float) >= min_safety]
    view = view[view["credit"].astype(float) >= min_credit]
    view = view[view["score"].astype(float) >= min_score]

    # ---------- 排序字段(可切换: 评分/收益风险/净收/安全垫/POP/EV) ----------
    sort_opts = {
        "评分": "score",
        "收益/风险": "reward_risk",
        "可成交净收": "credit",
        "安全垫": "safety_margin",
        "盈利概率POP": "pop",
        "期望收益EV": "ev",
    }
    sort_key = st.sidebar.selectbox("排序字段", list(sort_opts.keys()))
    sort_asc = st.sidebar.radio("排序方向", ["降序", "升序"]) == "升序"
    _sort_col = sort_opts[sort_key]
    view = view.sort_values(_sort_col, ascending=sort_asc, na_position="last")

    # ---------- Top10 排行榜 ----------
    st.subheader("🏆 Top 10 Put 信用价差排行榜")
    top = view.head(10)
    if top.empty:
        st.info("当前过滤条件下无结果，请放宽条件。")
    else:
        display = top[
            [
                "underlying_name", "label_display", "credit", "max_profit", "max_loss",
                "breakeven", "safety_margin", "reward_risk", "pop", "ev",
                "sell_iv", "sell_delta", "score", "tier", "consec_hits", "width",
            ]
        ].copy()
        display.columns = [
            "标的", "组合", "可成交净收", "最大盈利", "最大亏损", "BE", "安全垫",
            "收益/风险", "POP", "EV", "IV", "Delta", "评分", "状态", "连续满足", "宽度",
        ]
        # 关键: 底层保留数值(百分比列 ×100), 由 NumberColumn 负责显示格式化,
        # 这样点击列头排序按数值排序, 修复"收益/风险比无法正常排序"的问题。
        for _c in ["可成交净收", "最大盈利", "最大亏损", "BE", "安全垫", "收益/风险",
                   "POP", "EV", "IV", "Delta", "评分"]:
            display[_c] = pd.to_numeric(display[_c], errors="coerce")
        for _c in ["安全垫", "收益/风险", "IV"]:
            display[_c] = display[_c] * 100.0
        display["POP"] = display["POP"] * 100.0
        display["连续满足"] = pd.to_numeric(display["连续满足"], errors="coerce").fillna(0).astype(int)

        def _color_tier(v):
            return f"color: {TIER_COLOR.get(v, '#95a5a6')}; font-weight: bold"

        st.dataframe(
            display.style.map(_color_tier, subset=["状态"]),
            width="stretch", hide_index=True,
            column_config={
                "可成交净收": st.column_config.NumberColumn(format="%.4f"),
                "最大盈利": st.column_config.NumberColumn(format="¥%.0f"),
                "最大亏损": st.column_config.NumberColumn(format="¥%.0f"),
                "BE": st.column_config.NumberColumn(format="%.4f"),
                "安全垫": st.column_config.NumberColumn(format="%.2f%%"),
                "收益/风险": st.column_config.NumberColumn(format="%.2f%%"),
                "POP": st.column_config.NumberColumn(format="%.1f%%"),
                "EV": st.column_config.NumberColumn(format="¥%.0f"),
                "IV": st.column_config.NumberColumn(format="%.1f%%"),
                "Delta": st.column_config.NumberColumn(format="%.3f"),
                "评分": st.column_config.NumberColumn(format="%.2f"),
                "连续满足": st.column_config.NumberColumn(format="%d"),
            },
        )

    # ---------- 明细 ----------
    with st.expander(f"查看全部 {len(view)} 个候选价差"):
        detail = view[
            [
                "underlying_name", "label_display", "expire_month", "width", "credit",
                "mid_credit", "max_profit", "max_loss", "breakeven", "safety_margin",
                "reward_risk", "pop", "ev", "total_slippage", "sell_delta", "sell_iv",
                "score", "tier", "suggested_lots",
            ]
        ].copy()
        detail.columns = [
            "标的", "组合", "到期月", "宽度", "净收", "mid净收", "最大盈利", "最大亏损",
            "BE", "安全垫", "收益/风险", "POP", "EV", "总滑点", "Delta", "IV",
            "评分", "状态", "建议手数",
        ]
        for _c in ["宽度", "净收", "mid净收", "最大盈利", "最大亏损", "BE", "安全垫",
                   "收益/风险", "POP", "EV", "总滑点", "Delta", "IV", "评分"]:
            detail[_c] = pd.to_numeric(detail[_c], errors="coerce")
        for _c in ["安全垫", "收益/风险", "IV"]:
            detail[_c] = detail[_c] * 100.0
        detail["POP"] = detail["POP"] * 100.0
        detail["建议手数"] = pd.to_numeric(detail["建议手数"], errors="coerce").fillna(0).astype(int)
        st.dataframe(
            detail, width="stretch", hide_index=True,
            column_config={
                "宽度": st.column_config.NumberColumn(format="%.2f"),
                "净收": st.column_config.NumberColumn(format="%.4f"),
                "mid净收": st.column_config.NumberColumn(format="%.4f"),
                "最大盈利": st.column_config.NumberColumn(format="¥%.0f"),
                "最大亏损": st.column_config.NumberColumn(format="¥%.0f"),
                "BE": st.column_config.NumberColumn(format="%.4f"),
                "安全垫": st.column_config.NumberColumn(format="%.2f%%"),
                "收益/风险": st.column_config.NumberColumn(format="%.2f%%"),
                "POP": st.column_config.NumberColumn(format="%.1f%%"),
                "EV": st.column_config.NumberColumn(format="¥%.0f"),
                "总滑点": st.column_config.NumberColumn(format="%.4f"),
                "Delta": st.column_config.NumberColumn(format="%.3f"),
                "IV": st.column_config.NumberColumn(format="%.1f%%"),
                "评分": st.column_config.NumberColumn(format="%.2f"),
                "建议手数": st.column_config.NumberColumn(format="%d"),
            },
        )

    # ---------- 最近报警 ----------
    st.subheader("🚨 最近报警记录")
    if not alerts:
        st.caption("暂无报警。报警需同一组合连续 3 次扫描满足条件。")
    else:
        alert_df = pd.DataFrame(alerts)
        alert_df["标的"] = alert_df.apply(
            lambda r: UNDERLYING_NAMES.get(str(r.get("underlying") or ""),
                                           str(r.get("underlying") or "旧版V1.1")),
            axis=1,
        )
        alert_df["组合"] = alert_df.apply(
            lambda r: f"{float(r['sell_strike']):.2f}/{float(r['buy_strike']):.2f}P", axis=1
        )
        alert_df = alert_df[
            ["scan_id", "标的", "组合", "credit", "max_profit", "max_loss", "breakeven",
             "safety_margin", "reward_risk", "score", "tier", "spot"]
        ]
        alert_df.columns = ["扫描ID", "标的", "组合", "净收", "最大盈利", "最大亏损", "BE",
                            "安全垫", "收益/风险", "评分", "状态", "当时现价"]
        alert_df["净收"] = alert_df["净收"].apply(_fmt)
        alert_df["最大盈利"] = alert_df["最大盈利"].apply(lambda x: f"¥{float(x):,.0f}" if x != "-" else "-")
        alert_df["最大亏损"] = alert_df["最大亏损"].apply(lambda x: f"¥{float(x):,.0f}" if x != "-" else "-")
        alert_df["BE"] = alert_df["BE"].apply(_fmt)
        alert_df["安全垫"] = alert_df["安全垫"].apply(_pct)
        alert_df["收益/风险"] = alert_df["收益/风险"].apply(_pct)
        alert_df["评分"] = alert_df["评分"].apply(_fmt, nd=2)
        alert_df["当时现价"] = alert_df["当时现价"].apply(_fmt)
        st.dataframe(alert_df, width="stretch", hide_index=True)

    # ---------- 扫描历史 ----------
    with st.expander("扫描历史"):
        hist = DB.scan_history(limit=40)
        if hist:
            h = pd.DataFrame(hist)
            h["标的"] = h.apply(
                lambda r: UNDERLYING_NAMES.get(str(r.get("underlying") or ""),
                                               str(r.get("underlying") or "旧版V1.1")),
                axis=1,
            )
            h = h.rename(columns={
                "scan_time": "时间", "source": "源", "spot": "现价",
                "data_fresh": "新鲜", "n_contracts": "合约数",
                "n_spreads": "候选数", "n_alerts": "报警数",
            })
            h = h[["id", "时间", "标的", "源", "现价", "新鲜", "合约数", "候选数", "报警数"]]
            h["现价"] = h["现价"].apply(_fmt)
            h["新鲜"] = h["新鲜"].apply(lambda x: "是" if x else "否")
            st.dataframe(h, width="stretch", hide_index=True)

    st.caption(
        "⚠️ 本系统 V1.2 只扫描与提醒，绝不自动下单。可成交净收使用 Bid1−Ask1 保守口径；"
        "建议手数仅为风险预算参考，不代表任何交易指令。现货优先同花顺, 期权盘口来自新浪免费接口。"
    )


# 自动刷新: 每 N 秒重新渲染
def main():
    refresh = st.sidebar.slider("自动刷新(秒)", 5, 120, 30)
    render()
    import time
    time.sleep(refresh)
    st.rerun()


if __name__ == "__main__":
    main()

