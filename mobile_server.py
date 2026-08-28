# -*- coding: utf-8 -*-
"""
510500_mobile_radar/mobile_server.py
手机端实时看板轻量服务(纯 Python 标准库, 零第三方依赖)。

- 只读共享数据库: 默认读取同级 510500_put_scanner/data/scanner.db(不修改现有项目任何文件)
- GET /           -> 手机端 HTML 页面(移动优先, 自动刷新)
- GET /api/latest -> JSON: 各标的最新扫描 + Top 候选 + 报警 + 交易时段
- 供 ngrok/cloudflared 等内网穿透暴露到公网, 手机用互联网即可访问

用法:
    python mobile_server.py --port 8503 [--db <path>]
环境变量: SCANNER_MOBILE_DB(数据库路径), SCANNER_MOBILE_PORT(端口)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import urlparse

# 项目根
ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(ROOT, "static")

# 默认数据库: 同级现有扫描项目的库(只读)
DEFAULT_DB = os.path.normpath(
    os.path.join(ROOT, "..", "510500_put_scanner", "data", "scanner.db")
)

PORT = int(os.environ.get("SCANNER_MOBILE_PORT", "8503"))
CAND_LIMIT_PER_UNDER = 8  # 每标的仅返回评分 Top N 候选, 减载提速


# ------------------------------------------------------------------
# 交易时段判断(A 股: 工作日 09:30-11:30 / 13:00-15:00)
# ------------------------------------------------------------------
BJ_TZ = dt.timezone(dt.timedelta(hours=8))  # 服务器时间统一显示为北京时间


def trading_session(now: Optional[dt.datetime] = None) -> dict:
    now = now or dt.datetime.now(BJ_TZ)
    if now.weekday() >= 5:
        state = "closed"
    else:
        t = now.hour * 60 + now.minute
        state = "trading" if (
            (9 * 60 + 30) <= t <= (11 * 60 + 30) or (13 * 60) <= t <= (15 * 60)
        ) else "closed"
    return {
        "state": state,
        "label": "交易时段" if state == "trading" else "非交易时段",
        "server_time": now.strftime("%Y-%m-%d %H:%M:%S"),
    }


# ------------------------------------------------------------------
# 数据库只读读取
# ------------------------------------------------------------------
def _query(conn: sqlite3.Connection, sql: str, args=()):
    cur = conn.execute(sql, args)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def load_latest(db_path: str) -> dict:
    """读取每个标的最新【有效】扫描 + 全部候选 + 报警 + 历史概要"""
    out = {"ok": True, "db": os.path.basename(db_path), "scans": {}, "candidates": [],
           "alerts": [], "history": [], "scan_status": None}
    _stf = os.path.join(os.path.dirname(db_path) or ".", "scan_status.json")
    if os.path.exists(_stf):
        try:
            with open(_stf, encoding="utf-8") as _f:
                out["scan_status"] = json.load(_f)
        except Exception:
            pass
    if not os.path.exists(db_path):
        out["ok"] = False
        out["error"] = f"数据库不存在: {db_path}"
        return out
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
    except Exception as e:
        out["ok"] = False
        out["error"] = f"无法打开数据库(只读): {e}"
        return out
    try:
        codes = [r[0] for r in conn.execute(
            "SELECT underlying FROM scans GROUP BY underlying ORDER BY underlying")]
        for code in codes:
            row = conn.execute(
                "SELECT * FROM scans WHERE underlying=? AND n_contracts>0 AND spot>0 "
                "ORDER BY id DESC LIMIT 1", (code,)).fetchone()
            if not row:
                continue
            d = dict(row)
            try:
                _m = json.loads(d.get('meta') or '{}')
                if _m.get('underlying_name'):
                    d['underlying_name'] = _m['underlying_name']
            except Exception:
                pass
            out["scans"][code] = d
            # 该扫描的全部候选
            out["candidates"].extend(_query(
                conn,
                "SELECT * FROM spread_candidates WHERE scan_id=? ORDER BY score DESC LIMIT ?",
                (d["id"], CAND_LIMIT_PER_UNDER)))
        out["alerts"] = _query(
            conn, "SELECT * FROM spread_candidates WHERE alert_fired=1 "
                  "ORDER BY id DESC LIMIT 20")
        out["history"] = _query(
            conn, "SELECT id, underlying, scan_time, source, spot, n_contracts, "
                  "n_spreads, n_alerts FROM scans ORDER BY id DESC LIMIT 10")
    finally:
        conn.close()
    return out


# ------------------------------------------------------------------
# HTTP 服务
# ------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "510500MobileRadar/1.0"

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/api/latest", "/api/latest/"):
            data = load_latest(getattr(self.server, "db_path", DEFAULT_DB))
            data["session"] = trading_session()
            body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return
        if path in ("/api/log", "/api/log/"):
            log_lines = []
            _lf = os.path.join(ROOT, "logs", "scanner.log")
            if os.path.exists(_lf):
                try:
                    with open(_lf, "r", encoding="utf-8", errors="ignore") as _f:
                        log_lines = _f.readlines()[-80:]
                except Exception:
                    pass
            body = json.dumps({"ok": True, "log": "".join(log_lines)},
                              ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return
        if path in ("/", "/index.html", "/mobile"):
            html = os.path.join(STATIC_DIR, "index.html")
            if os.path.exists(html):
                with open(html, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
                return
            self._send(404, b"index.html missing", "text/plain; charset=utf-8")
            return
        if path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
            return
        self._send(404, b"not found", "text/plain; charset=utf-8")

    def log_message(self, fmt, *args):  # 静默访问日志
        pass


def main() -> None:
    ap = argparse.ArgumentParser(description="510500+588080 手机端实时看板服务")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--host", default="0.0.0.0")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"[警告] 数据库不存在: {args.db}")
        print("       请确认同级 510500_put_scanner 已扫描过(运行过 run_scanner.py 或点击看板按钮)。")
        print("       或用 --db <路径> 指定其他数据库。")

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.db_path = args.db
    print("手机端实时看板已启动:")
    print(f"  本机:   http://localhost:{args.port}")
    print(f"  局域网: http://<本机IP>:{args.port}  (手机连同一 WiFi 可直接访问)")
    print(f"  数据库: {args.db}")
    print("  互联网: 另开窗口运行 start_tunnel.bat 生成 ngrok 公网地址后, 手机用该地址访问。")
    print("  Ctrl+C 停止")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
