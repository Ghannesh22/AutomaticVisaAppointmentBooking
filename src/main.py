from __future__ import annotations

import asyncio
import signal
import sys

from src.browser import browser_page
from src.config_loader import load_config
from src.logger import setup_logger
from src.monitor import run_monitor
from src.state import has_success_flag, success_flag_path


async def async_main() -> int:
    logger = setup_logger()
    config = load_config()

    logger.info("Starting visa appointment monitor")
    logger.info("Target months: %s", ", ".join(config.target_months))
    logger.info("Headless: %s", config.headless)
    logger.info("Dry run: %s", config.dry_run)
    logger.info("Polling interval: %s seconds", config.check_interval_seconds)

    if has_success_flag():
        logger.warning(
            "Success flag exists at %s; stopping before browser start to prevent duplicate booking",
            success_flag_path(),
        )
        return 0

    async with browser_page(config) as page:
        success = await run_monitor(page, config, logger)

    if success:
        logger.info("Finished after successful booking submission")
        return 0
    logger.info("Finished without final booking submission")
    return 1


if __name__ == "__main__":
    def _raise_keyboard_interrupt(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _raise_keyboard_interrupt)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _raise_keyboard_interrupt)

    try:
        sys.exit(asyncio.run(async_main()))
    except KeyboardInterrupt:
        setup_logger().info("Ctrl+C received; shutdown completed")
        sys.exit(130)
