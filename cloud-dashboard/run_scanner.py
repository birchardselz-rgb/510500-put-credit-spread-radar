# -*- coding: utf-8 -*-
"""
run_scanner.py
510500 + 588080 Put 信用价差扫描器 V1.2 命令行入口。

用法:
  python run_scanner.py                 # 按 config.yaml 持续扫描全部启用的标的
  python run_scanner.py --mode mock     # 强制离线模拟模式(无网络可测)
  python run_scanner.py --mode live     # 强制实时行情
  python run_scanner.py --once          # 只扫一轮并退出
  python run_scanner.py --interval 5    # 覆盖扫描间隔(秒)
  python run_scanner.py --rounds 3      # 只跑 3 轮(演示)
  python run_scanner.py --underlying 588080  # 只扫指定标的
"""
from __future__ import annotations

import argparse
import os
import sys


def _bypass_stale_proxy() -> None:
    """绕过失效的系统/用户级代理环境变量。

    若用户级环境变量 HTTP_PROXY/HTTPS_PROXY/ALL_PROXY 指向已停止的本地代理
    (如 127.0.0.1:3067), requests/akshare 会尝试经该代理访问新浪等行情源而失败。
    此处仅对本进程移除, 不影响用户其他软件。
    """
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
              "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(k, None)
    # 显式标记无代理, 防止 requests 回退到系统代理
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"


def main() -> int:
    _bypass_stale_proxy()
    parser = argparse.ArgumentParser(description="510500+588080 Put 信用价差实时扫描器 V1.2")
    parser.add_argument("--mode", choices=["mock", "live"], default=None,
                        help="强制数据源模式(mock=离线模拟, live=实时)")
    parser.add_argument("--once", action="store_true", help="只扫描一轮后退出")
    parser.add_argument("--interval", type=float, default=None, help="覆盖扫描间隔(秒)")
    parser.add_argument("--rounds", type=int, default=None, help="扫描轮数上限(演示用)")
    parser.add_argument("--underlying", type=str, default=None, help="只扫描指定标的代码")
    args = parser.parse_args()

    from util import load_config, setup_logging, project_path
    os.chdir(project_path())  # 保证相对路径(data/logs)定位正确

    cfg = load_config()
    setup_logging(cfg)

    if args.mode:
        cfg["data_source"]["primary"] = "mock" if args.mode == "mock" else "tonghuashun"
    if args.underlying:
        # 只保留指定标的
        for code in list(cfg.get("underlyings", {}).keys()):
            if code != args.underlying:
                cfg["underlyings"][code]["enabled"] = False

    from core.engine import ScanEngine

    engine = ScanEngine(cfg)
    try:
        if args.once:
            results = engine.run_once()
            ok = True
            for code, r in results.items():
                engine._print_round(r)
                if r.error:
                    ok = False
            return 0 if ok else 1
        engine.run_forever(interval=args.interval, max_rounds=args.rounds)
        return 0
    except KeyboardInterrupt:
        print("\n[停止] 用户中断")
        return 0
    finally:
        engine.close()


if __name__ == "__main__":
    sys.exit(main())
