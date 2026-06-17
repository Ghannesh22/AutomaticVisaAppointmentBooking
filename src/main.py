from __future__ import annotations

import asyncio
import signal
import sys
from datetime import datetime, time

from src.browser import browser_page
from src.config_loader import load_config
from src.logger import setup_logger
from src.monitor import run_monitor
from src.state import has_success_flag
from src.telegram_notifier import refresh_telegram_subscribers, send_telegram_alert


async def async_main() -> int:
    logger = setup_logger()
    try:
        config = load_config()

        logger.info("Starting visa appointment monitor")
        logger.info("Target months: %s", ", ".join(config.target_months))
        logger.info("Headless: %s", config.headless)
        logger.info("Dry run: %s", config.dry_run)
        logger.info("Polling interval: %s seconds", config.check_interval_seconds)
        logger.info("Daily stop time: %s", config.stop_at_time.strftime("%H:%M"))
        subscriber_count = refresh_telegram_subscribers(force=True, logger=logger)
        if subscriber_count:
            logger.info("Telegram subscribers active: %s", subscriber_count)

        completed_profiles = [
            profile.name
            for profile in config.applicant_profiles
            if has_success_flag(profile.name)
        ]
        if len(completed_profiles) == len(config.applicant_profiles):
            logger.warning(
                "Success flags exist for all applicant profiles; stopping before browser start to prevent duplicate booking",
            )
            return 0
        if completed_profiles:
            logger.info("Already booked applicant profiles: %s", ", ".join(completed_profiles))

        remaining_seconds = _seconds_until_daily_stop(config.stop_at_time)
        if remaining_seconds <= 0:
            logger.info("Daily stop time already reached; stopping before browser start")
            return 1

        async with browser_page(config) as page:
            try:
                success = await asyncio.wait_for(
                    run_monitor(page, config, logger),
                    timeout=remaining_seconds,
                )
            except TimeoutError:
                logger.info("Daily stop time reached; stopping monitor")
                success = False

        if success:
            logger.info("Finished after successful booking submission")
            return 0
        logger.info("Finished without final booking submission")
        return 1
    except Exception as exc:
        logger.exception("Bot stopped because of an unexpected startup/runtime error")
        _send_unhandled_error_telegram_alert(exc, logger)
        return 1


def _seconds_until_daily_stop(stop_at_time: time) -> float:
    now = datetime.now()
    deadline = datetime.combine(now.date(), stop_at_time)
    return max(0.0, (deadline - now).total_seconds())


def _send_unhandled_error_telegram_alert(exc: Exception, logger) -> None:
    if send_telegram_alert(
        title="Bot stopped because of an unexpected error",
        message=str(exc),
        severity="fatal",
        force=True,
    ):
        logger.info("Telegram unhandled error alert sent")


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
