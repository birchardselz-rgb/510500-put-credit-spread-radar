# -*- coding: utf-8 -*-
"""
util.py
通用工具: 配置加载、日志初始化、项目路径。
"""
from __future__ import annotations

import logging
import logging.handlers
import os
from typing import Optional

import yaml

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.yaml")


def project_path(*parts: str) -> str:
    return os.path.join(PROJECT_ROOT, *parts)


def load_config(path: Optional[str] = None) -> dict:
    """加载 config.yaml; 文件缺失时抛出清晰错误。"""
    p = path or CONFIG_PATH
    if not os.path.exists(p):
        raise FileNotFoundError(
            f"配置文件不存在: {p}\n请确认在项目根目录运行。"
        )
    with open(p, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    # 保证关键节存在
    cfg.setdefault("data_source", {})
    cfg.setdefault("contracts", {})
    cfg.setdefault("account", {})
    cfg.setdefault("strategy", {})
    cfg.setdefault("scoring", {})
    cfg.setdefault("alerts", {})
    cfg.setdefault("storage", {})
    cfg.setdefault("logging", {})
    cfg.setdefault("mock", {})
    return cfg


def setup_logging(cfg: dict) -> None:
    """初始化文件 + 控制台日志, 关键异常不导致主循环退出。"""
    lg = cfg.get("logging", {})
    level = getattr(logging, str(lg.get("level", "INFO")).upper(), logging.INFO)
    log_file = project_path(lg.get("file", "logs/scanner.log"))
    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=int(lg.get("max_bytes", 10485760)),
        backupCount=int(lg.get("backup_count", 5)),
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)
