# -*- coding: utf-8 -*-
"""
storage/database.py
SQLite 持久化层。

- 每次扫描保存: 扫描快照(标的价格/时间)、全部期权盘口、全部候选价差结果
- 表结构使用通用 SQL 类型(INTEGER/REAL/TEXT/BOOLEAN), 便于未来升级 PostgreSQL
- 线程安全: 引擎与看板可能不同进程同时读写, 使用 WAL + busy_timeout
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from typing import List, Optional

from util import project_path

log = logging.getLogger("scanner.storage")

SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    underlying  TEXT,
    scan_time   TEXT NOT NULL,
    source      TEXT NOT NULL,
    spot        REAL,
    spot_time   TEXT,
    data_fresh  INTEGER,
    n_contracts INTEGER,
    n_spreads   INTEGER,
    n_alerts    INTEGER,
    months      TEXT,
    meta        TEXT
);

CREATE TABLE IF NOT EXISTS option_quotes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id         INTEGER NOT NULL,
    underlying      TEXT,
    contract_code   TEXT,
    trade_code      TEXT,
    contract_name   TEXT,
    cp              TEXT,
    strike          REAL,
    expiry_date     TEXT,
    days_to_expiry  INTEGER,
    is_adjusted     INTEGER,
    bid1            REAL,
    ask1            REAL,
    bid1_vol        INTEGER,
    ask1_vol        INTEGER,
    last            REAL,
    volume          INTEGER,
    open_interest   INTEGER,
    iv              REAL,
    delta           REAL,
    gamma           REAL,
    theta           REAL,
    vega            REAL,
    quote_time      TEXT,
    valid           INTEGER,
    invalid_reason  TEXT,
    FOREIGN KEY (scan_id) REFERENCES scans(id)
);
CREATE INDEX IF NOT EXISTS idx_quotes_scan ON option_quotes(scan_id);

CREATE TABLE IF NOT EXISTS spread_candidates (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id          INTEGER NOT NULL,
    underlying       TEXT,
    expire_month     TEXT,
    sell_code        TEXT,
    buy_code         TEXT,
    sell_name        TEXT,
    buy_name         TEXT,
    sell_strike      REAL,
    buy_strike       REAL,
    width            REAL,
    credit           REAL,
    mid_credit       REAL,
    max_profit       REAL,
    max_loss         REAL,
    breakeven        REAL,
    safety_margin    REAL,
    reward_risk      REAL,
    total_slippage   REAL,
    sell_delta       REAL,
    sell_iv          REAL,
    buy_delta        REAL,
    buy_iv           REAL,
    score            REAL,
    tier             TEXT,
    suggested_lots   INTEGER,
    account_risk_pct REAL,
    consec_hits      INTEGER,
    alert_fired      INTEGER,
    spot             REAL,
    FOREIGN KEY (scan_id) REFERENCES scans(id)
);
CREATE INDEX IF NOT EXISTS idx_spreads_scan ON spread_candidates(scan_id);
CREATE INDEX IF NOT EXISTS idx_spreads_score ON spread_candidates(score);
"""

# V1.2 新增 underlying 列的迁移(老库补列)
_MIGRATIONS = [
    ("scans", "underlying", "TEXT"),
    ("option_quotes", "underlying", "TEXT"),
    ("spread_candidates", "underlying", "TEXT"),
]


class Database:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or project_path("data", "scanner.db")
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    def _migrate(self) -> None:
        """老库(无 underlying 列)自动补列"""
        for table, col, ctype in _MIGRATIONS:
            try:
                cur = self._conn.execute(f"PRAGMA table_info({table})")
                cols = [row[1] for row in cur.fetchall()]
                if col not in cols:
                    self._conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {col} {ctype}"
                    )
                    log.info("迁移: %s 新增列 %s", table, col)
            except Exception as e:
                log.warning("迁移 %s.%s 失败: %s", table, col, e)

    # ------------------------------------------------------------------
    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _executemany(self, sql: str, seq) -> None:
        with self._lock:
            self._conn.executemany(sql, seq)
            self._conn.commit()

    # ------------------------------------------------------------------
    def save_scan(
        self,
        scan_time: str,
        source: str,
        spot: float,
        spot_time: str,
        data_fresh: bool,
        months: List[str],
        contracts: list,
        spreads: list,
        n_alerts: int,
        underlying: str = "",
        underlying_name: str = "",
    ) -> int:
        """保存一次完整扫描, 返回 scan_id"""
        cur = self._execute(
            """INSERT INTO scans
               (underlying, scan_time, source, spot, spot_time, data_fresh,
                n_contracts, n_spreads, n_alerts, months, meta)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                underlying, scan_time, source, spot, spot_time, int(data_fresh),
                len(contracts), len(spreads), n_alerts,
                json.dumps(months, ensure_ascii=False),
                json.dumps({"underlying_name": underlying_name,
                            "note": "510500+588080 Put credit spread scanner V1.2"},
                           ensure_ascii=False),
            ),
        )
        scan_id = cur.lastrowid

        q_rows = []
        for c in contracts:
            d = c.to_dict() if hasattr(c, "to_dict") else c
            q_rows.append((
                scan_id, underlying, d["code"], d["trade_code"], d["name"], d["cp"],
                d["strike"], d["expiry_date"], d["days_to_expiry"],
                int(d["is_adjusted"]), d["bid"], d["ask"], d["bid_vol"],
                d["ask_vol"], d["last"], d["volume"], d["open_interest"],
                d["iv"], d["delta"], d["gamma"], d["theta"], d["vega"],
                d["quote_time"], int(d["valid"]), d["invalid_reason"],
            ))
        if q_rows:
            self._executemany(
                """INSERT INTO option_quotes
                   (scan_id, underlying, contract_code, trade_code, contract_name, cp,
                    strike, expiry_date, days_to_expiry, is_adjusted, bid1, ask1,
                    bid1_vol, ask1_vol, last, volume, open_interest, iv, delta,
                    gamma, theta, vega, quote_time, valid, invalid_reason)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                q_rows,
            )

        s_rows = []
        for s in spreads:
            d = s.to_dict() if hasattr(s, "to_dict") else s
            s_rows.append((
                scan_id, underlying or d.get("underlying", ""),
                d["expire_month"], d["sell_code"], d["buy_code"],
                d["sell_name"], d["buy_name"], d["sell_strike"], d["buy_strike"],
                d["width"], d["credit"], d["mid_credit"], d["max_profit"],
                d["max_loss"], d["breakeven"], d["safety_margin"], d["reward_risk"],
                d["total_slippage"], d["sell_delta"], d["sell_iv"], d["buy_delta"],
                d["buy_iv"], d.get("score", 0.0), d.get("tier", ""),
                d.get("suggested_lots", 0), d.get("account_risk_pct", 0.0),
                d.get("consec_hits", 0), int(d.get("alert_fired", False)),
                d.get("spot", spot),
            ))
        if s_rows:
            self._executemany(
                """INSERT INTO spread_candidates
                   (scan_id, underlying, expire_month, sell_code, buy_code,
                    sell_name, buy_name, sell_strike, buy_strike, width, credit,
                    mid_credit, max_profit, max_loss, breakeven, safety_margin,
                    reward_risk, total_slippage, sell_delta, sell_iv, buy_delta,
                    buy_iv, score, tier, suggested_lots, account_risk_pct,
                    consec_hits, alert_fired, spot)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                s_rows,
            )
        return scan_id

    # ------------------------------------------------------------------
    def latest_scan(self) -> Optional[dict]:
        cur = self._conn.execute(
            "SELECT * FROM scans ORDER BY id DESC LIMIT 1"
        )
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    def latest_scan_for(self, underlying: str) -> Optional[dict]:
        """指定标的的最新一次扫描"""
        cur = self._conn.execute(
            "SELECT * FROM scans WHERE underlying=? ORDER BY id DESC LIMIT 1",
            (underlying,),
        )
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    def latest_valid_scan_for(self, underlying: str) -> Optional[dict]:
        """指定标的最新一次【有效】扫描(排除 spot<=0 / 无合约的空行, 兜底用)"""
        cur = self._conn.execute(
            "SELECT * FROM scans WHERE underlying=? AND n_contracts>0 AND spot>0 "
            "ORDER BY id DESC LIMIT 1",
            (underlying,),
        )
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    def latest_spreads(self, limit: int = 50) -> list:
        cur = self._conn.execute(
            "SELECT * FROM spread_candidates ORDER BY id DESC LIMIT ?", (limit,)
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def spreads_by_scan(self, scan_id: int, min_score: float = 0.0) -> list:
        cur = self._conn.execute(
            "SELECT * FROM spread_candidates WHERE scan_id=? AND score>=? "
            "ORDER BY score DESC", (scan_id, min_score)
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def recent_alerts(self, limit: int = 30) -> list:
        cur = self._conn.execute(
            "SELECT * FROM spread_candidates WHERE alert_fired=1 "
            "ORDER BY id DESC LIMIT ?", (limit,)
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def scan_history(self, limit: int = 50) -> list:
        cur = self._conn.execute(
            "SELECT id, underlying, scan_time, source, spot, data_fresh, n_contracts, "
            "n_spreads, n_alerts FROM scans ORDER BY id DESC LIMIT ?", (limit,)
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def stats(self) -> dict:
        cur = self._conn.execute(
            "SELECT COUNT(*) FROM scans"
        )
        n_scans = cur.fetchone()[0]
        cur = self._conn.execute(
            "SELECT COUNT(*) FROM spread_candidates"
        )
        n_spreads = cur.fetchone()[0]
        cur = self._conn.execute(
            "SELECT COUNT(DISTINCT scan_id) FROM spread_candidates WHERE alert_fired=1"
        )
        n_alert_scans = cur.fetchone()[0]
        return {"scans": n_scans, "spread_rows": n_spreads, "alert_scans": n_alert_scans}
