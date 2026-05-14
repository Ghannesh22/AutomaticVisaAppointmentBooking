from __future__ import annotations

import logging
from pathlib import Path

from src.config_loader import PROJECT_ROOT


def setup_logger() -> logging.Logger:
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(exist_ok=True)

    logger = logging.getLogger("visa_appointment")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    console_formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%H:%M:%S",
    )
    file_formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setFormatter(console_formatter)
    logger.addHandler(console)

    file_handler = logging.FileHandler(log_dir / "monitor.log", encoding="utf-8")
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    return logger


def safe_filename(prefix: str, suffix: str = ".png") -> Path:
    from datetime import datetime

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return PROJECT_ROOT / "screenshots" / f"{prefix}_{stamp}{suffix}"
