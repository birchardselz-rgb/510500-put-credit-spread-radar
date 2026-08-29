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
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="510500+588080 Put 信用价差实时扫描器 V1.2")
    parser.add_argument("--mode", choices=["mock", "live"], default=None,
                        help="强制数据源模式(mock=离线模拟, live=实时)")
    parser.add_argument("--once", action="store_true", help="只扫描一轮后退出")
    parser.add_argument("--interval", type=float, default=None, help="覆盖扫描间隔(秒)")
    parser.add_argument("--rounds", type=int, default=None, help="扫描轮数上限(演示用)")
    parser.add_argument("--underlying", type=str, default=None, help="只扫描指定标的代码")
    args = parser.parse_args()

    from util import load_config, setup_logging, project_path
    import os
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
