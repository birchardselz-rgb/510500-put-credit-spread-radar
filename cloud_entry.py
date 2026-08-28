# -*- coding: utf-8 -*-
"""
cloud_entry.py
云端统一入口: 后台扫描线程 + 手机端服务, 供 Docker/HF Spaces/Render 等部署。

- 扫描线程: 每 scan_interval_seconds 秒扫描全部启用标的, 写入云端本地 DB(data/scanner.db)
- 手机服务: 只读该 DB, 提供手机端 HTML 页面与 /api/latest JSON
- 任一异常不退出主循环, 保证 24/7 自愈
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time

# 云端容器默认 UTC, 统一为北京时间: 影响扫描时间戳/交易时段/服务器时间显示
os.environ.setdefault('TZ', 'Asia/Shanghai')
try:
    time.tzset()
except (AttributeError, Exception):
    pass  # Windows 无 tzset, 本地时区本来就是北京时间

# 保证以本项目根为工作目录(相对路径 data/logs 定位正确)
ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)


def _scan_loop(cfg: dict, interval: float) -> None:
    from util import setup_logging

    setup_logging(cfg)
    from core.engine import ScanEngine

    engine = ScanEngine(cfg)
    logger = logging.getLogger("cloud.scan")
    while True:
        try:
            results = engine.run_once()
            for code, r in results.items():
                engine._print_round(r)
        except Exception as e:  # noqa: BLE001 - 关键: 单轮失败不退出主循环
            logger.exception("扫描轮次失败: %s", e)
        time.sleep(max(interval, 5.0))


def main() -> int:
    from util import load_config

    cfg = load_config()
    interval = float(cfg.get("data_source", {}).get("scan_interval_seconds", 10))

    # 后台扫描线程
    t = threading.Thread(target=_scan_loop, args=(cfg, interval), daemon=True)
    t.start()
    print(f"[cloud_entry] 扫描线程已启动, 间隔 {interval}s")

    # 手机端服务(只读云端 DB)
    import mobile_server

    db_path = cfg.get("storage", {}).get("database", "data/scanner.db")
    port = int(os.environ.get("PORT", "8503"))
    sys.argv = ["cloud_entry", "--port", str(port), "--db", db_path]
    mobile_server.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
