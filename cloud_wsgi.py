# -*- coding: utf-8 -*-
"""
cloud_wsgi.py
PythonAnywhere(免费档, Web 应用常驻不睡眠)专用 WSGI 入口。

- 进程启动时拉起后台扫描线程(每 N 秒扫双标的, 写 data/scanner.db)
- GET /            -> 手机端页面(static/index.html)
- GET /api/latest  -> JSON(与 mobile_server 同口径)
用法: PythonAnywhere Web 标签, Manual configuration, WSGI 指向本文件。
"""
from __future__ import annotations

import json
import os
import sys
import threading

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from mobile_server import load_latest, trading_session  # noqa: E402

_STATIC = os.path.join(ROOT, "static", "index.html")


def application(environ, start_response):  # noqa: C901
    path = environ.get("PATH_INFO") or "/"
    if path.startswith("/api/"):
        data = load_latest(os.path.join(ROOT, "data", "scanner.db"))
        data["session"] = trading_session()
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        start_response("200 OK", [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
        ])
        return [body]
    if path in ("/", "/index.html", "/mobile"):
        try:
            with open(_STATIC, "rb") as f:
                html = f.read()
        except OSError:
            start_response("500 Internal Server Error", [("Content-Type", "text/plain; charset=utf-8")])
            return [b"static/index.html missing"]
        start_response("200 OK", [
            ("Content-Type", "text/html; charset=utf-8"),
            ("Cache-Control", "no-store"),
        ])
        return [html]
    start_response("404 Not Found", [("Content-Type", "text/plain; charset=utf-8")])
    return [b"not found"]


# ---------------- 后台扫描线程(进程常驻则持续运行) ----------------
def _scan_loop():
    try:
        import time
        from util import load_config
        from core.engine import ScanEngine

        cfg = load_config()
        interval = float(cfg.get("data_source", {}).get("scan_interval_seconds", 10))
        engine = ScanEngine(cfg)
        while True:
            try:
                results = engine.run_once()
                for _code, r in results.items():
                    if r.error:
                        print("[scan] error:", r.error)
            except Exception as e:  # noqa: BLE001 - 自愈
                print("[scan] round failed:", e)
            time.sleep(max(interval, 5.0))
    except Exception as e:  # noqa: BLE001
        print("[scan] thread exited:", e)


if not getattr(_scan_loop, "_started", False):
    _scan_loop._started = True
    threading.Thread(target=_scan_loop, daemon=True).start()
    print("[cloud_wsgi] scanner thread started")
