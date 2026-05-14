from __future__ import annotations

import asyncio
import sys

from src.browser import browser_page
from src.config_loader import load_config
from src.logger import setup_logger
from src.monitor import run_monitor


async def async_main() -> int:
    logger = setup_logger()
    config = load_config()

    logger.info("Starting visa appointment monitor")
    logger.info("Target month: %s", config.target_month)
    logger.info("Headless: %s", config.headless)
    logger.info("Dry run: %s", config.dry_run)
    logger.info("Polling interval: %s seconds", config.check_interval_seconds)

    async with browser_page(config) as page:
        success = await run_monitor(page, config, logger)

    if success:
        logger.info("Finished after successful booking submission")
        return 0
    logger.info("Finished without final booking submission")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(async_main()))
