from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from logging import Logger

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from src.booker import book_slot
from src.config_loader import AppConfig
from src.email_notifier import send_heartbeat_email
from src.logger import safe_filename
from src.slot_checker import check_july_slot
from src.state import has_success_flag, success_flag_path


class FlowRestartRequired(RuntimeError):
    pass


@dataclass
class MonitoringStats:
    started_at: datetime = field(default_factory=datetime.now)
    total_checks: int = 0
    total_browser_restarts: int = 0
    total_failures: int = 0
    last_successful_calendar_load: datetime | None = None
    current_wait_seconds: int = 0
    current_retry_count: int = 0
    state: str = "starting"
    open_months_seen: set[str] = field(default_factory=set)

    def summary(self) -> str:
        uptime = datetime.now() - self.started_at
        last_load = (
            self.last_successful_calendar_load.isoformat(timespec="seconds")
            if self.last_successful_calendar_load
            else "never"
        )
        return (
            "monitor_summary | state=%s | uptime=%s | total_checks=%s | "
            "browser_restarts=%s | failures=%s | retry_count=%s | "
            "current_wait_seconds=%s | last_successful_calendar_load=%s | "
            "open_months_seen=%s"
            % (
                self.state,
                str(uptime).split(".")[0],
                self.total_checks,
                self.total_browser_restarts,
                self.total_failures,
                self.current_retry_count,
                self.current_wait_seconds,
                last_load,
                ",".join(sorted(self.open_months_seen)) or "none",
            )
        )


async def run_monitor(page: Page, config: AppConfig, logger: Logger) -> bool:
    stats = MonitoringStats(current_wait_seconds=config.check_interval_seconds)
    last_heartbeat_sent = datetime.now()

    if has_success_flag():
        stats.state = "success_flag_present"
        logger.warning(
            "Success flag exists at %s; stopping before monitoring to prevent duplicate booking",
            success_flag_path(),
        )
        logger.info(stats.summary())
        return True

    deadline = datetime.now() + timedelta(minutes=config.max_runtime_minutes)
    attempt = 1
    failure_count = 0
    base_backoff_seconds = min(60, max(5, config.check_interval_seconds // 4))
    max_backoff_seconds = max(config.check_interval_seconds, base_backoff_seconds)

    try:
        while datetime.now() < deadline:
            if has_success_flag():
                stats.state = "success_flag_present"
                logger.warning(
                    "Success flag exists at %s; stopping monitor to prevent duplicate booking",
                    success_flag_path(),
                )
                return True

            stats.state = "starting_flow"
            logger.info(
                "monitor_state | state=%s | attempt=%s | retry_count=%s | wait_seconds=%s",
                stats.state,
                attempt,
                stats.current_retry_count,
                stats.current_wait_seconds,
            )
            try:
                await navigate_to_calendar(page, config, logger)
                stats.last_successful_calendar_load = datetime.now()
                if failure_count:
                    logger.info("Page load succeeded; resetting failure backoff")
                failure_count = 0
                stats.current_retry_count = 0
                stats.current_wait_seconds = config.check_interval_seconds

                while datetime.now() < deadline:
                    if has_success_flag():
                        stats.state = "success_flag_present"
                        logger.warning(
                            "Success flag exists at %s; stopping monitor to prevent duplicate booking",
                            success_flag_path(),
                        )
                        return True

                    stats.state = "checking_calendar"
                    await _ensure_calendar_page(page)
                    stats.last_successful_calendar_load = datetime.now()
                    result = await check_july_slot(page, config, logger)
                    stats.total_checks += 1
                    _record_open_month_observations(stats, result.month_observations, config, logger)
                    logger.info(
                        "monitor_state | state=%s | checks=%s | result=%s | retry_count=%s | wait_seconds=%s",
                        stats.state,
                        stats.total_checks,
                        result.status,
                        stats.current_retry_count,
                        stats.current_wait_seconds,
                    )

                    if await _maybe_send_heartbeat(config, stats, logger, last_heartbeat_sent):
                        last_heartbeat_sent = datetime.now()

                    if result.slot:
                        stats.state = "slot_detected"
                        logger.info(
                            "slot_detection_result | valid target-month slot detected | target_month=%s",
                            config.target_month,
                        )
                        success = await book_slot(page, config, result.slot, logger)
                        if success:
                            stats.state = "booking_submitted"
                            return True
                        stats.state = "dry_run_complete"
                        logger.info("Dry-run reached booking boundary; stopping monitor")
                        return False

                    logger.info("slot_detection_result | %s", result.status)
                    stats.state = "waiting"
                    stats.current_wait_seconds = config.check_interval_seconds
                    logger.info(
                        "monitor_state | state=waiting | wait_seconds=%s | next_action=reload_calendar",
                        stats.current_wait_seconds,
                    )
                    await asyncio.sleep(config.check_interval_seconds)
                    await page.reload(wait_until="networkidle")
            except ValueError as exc:
                stats.state = "fatal_error"
                stats.total_failures += 1
                screenshot = safe_filename("error")
                try:
                    await page.screenshot(path=str(screenshot), full_page=True)
                    logger.error("Configuration or form-fill error: %s. Saved screenshot: %s", exc, screenshot)
                except Exception:
                    logger.error("Configuration or form-fill error before screenshot could be saved: %s", exc)
                return False
            except (FlowRestartRequired, PlaywrightTimeoutError) as exc:
                stats.state = "navigation_failure"
                stats.total_failures += 1
                screenshot = safe_filename("error")
                try:
                    await page.screenshot(path=str(screenshot), full_page=True)
                    logger.error("Flow failed: %s. Saved screenshot: %s", exc, screenshot)
                except Exception:
                    logger.error("Flow failed before screenshot could be saved: %s", exc)
                attempt += 1
                failure_count += 1
                stats.current_retry_count = failure_count
                backoff_seconds = min(
                    max_backoff_seconds,
                    base_backoff_seconds * (2 ** (failure_count - 1)),
                )
                stats.current_wait_seconds = backoff_seconds
                stats.total_browser_restarts += 1
                logger.info(
                    "browser_restart_event | count=%s | reason=navigation_failure | next_attempt=%s",
                    stats.total_browser_restarts,
                    attempt,
                )
                logger.info(
                    "monitor_state | state=backoff | retry_count=%s | wait_seconds=%s",
                    failure_count,
                    backoff_seconds,
                )
                if await _maybe_send_heartbeat(config, stats, logger, last_heartbeat_sent):
                    last_heartbeat_sent = datetime.now()
                await asyncio.sleep(backoff_seconds)
                continue

        stats.state = "max_runtime_reached"
        logger.info("Max runtime reached without a successful booking")
        return False
    except asyncio.CancelledError:
        stats.state = "cancelled"
        logger.info("Shutdown requested; stopping monitor cleanly")
        raise
    finally:
        logger.info(stats.summary())


async def navigate_to_calendar(page: Page, config: AppConfig, logger: Logger) -> None:
    logger.info("Opening website: %s", config.website_url)
    await page.goto(config.website_url, wait_until="networkidle")
    await _accept_cookies_if_present(page, logger)

    logger.info("Step 1: selecting Ausländer- und Staatsangehörigkeitsbehörde")
    await _click_first_visible(
        page,
        [
            "#buttonfunktionseinheit-1",
            "button[name='Ausländer- und Staatsangehörigkeitsbehörde']",
            "button:has-text('Ausländer- und Staatsangehörigkeitsbehörde')",
        ],
    )
    await page.wait_for_load_state("networkidle")

    logger.info("Step 2: selecting location %s", config.appointment_location)
    await _choose_location_or_section(page, config.appointment_location)

    logger.info("Step 2: selecting category %s and applicant count 1", config.visa_category)
    await _select_category_count(page, config.visa_category)

    logger.info("Step 2: clicking Weiter")
    await _click_first_visible(page, ["#WeiterButton", "input[value='Weiter']", "button:has-text('Weiter')"])
    await page.wait_for_load_state("networkidle")

    logger.info("Step 2 popup: accepting info dialog with OK")
    await _click_ok_if_present(page)
    await page.wait_for_load_state("networkidle")

    logger.info("Step 3: continuing past location/address page")
    await _click_first_visible(page, ["#WeiterButton", "input[value='Weiter']", "button:has-text('Weiter')"])
    await page.wait_for_load_state("networkidle")

    await _ensure_calendar_page(page)
    logger.info("Step 4: reached appointment calendar/slot page")


async def _accept_cookies_if_present(page: Page, logger: Logger) -> None:
    button = page.locator("#cookie_msg_btn_yes, input[value='Akzeptieren'], button:has-text('Akzeptieren')").first
    if await button.count() and await button.is_visible():
        logger.info("Accepting cookie banner")
        await button.click()


async def _choose_location_or_section(page: Page, text: str) -> None:
    selectors = [
        f"text={text}",
        f"button:has-text('{text}')",
        f"summary:has-text('{text}')",
        f"label:has-text('{text}')",
        f"h3:has-text('{text}')",
    ]
    for selector in selectors:
        locator = page.locator(selector).first
        if await locator.count() and await locator.is_visible():
            await locator.click()
            return
    normalized = _normalize_selector_text(text)
    clicked = await page.evaluate(
        """target => {
            const normalize = value => (value || '')
                .toLowerCase()
                .normalize('NFD')
                .replace(/[\\u0300-\\u036f]/g, '')
                .replace(/[^a-z0-9]+/g, ' ')
                .trim();
            const elements = Array.from(document.querySelectorAll('button, summary, label, h3, div'));
            const match = elements.find(el => normalize(el.innerText || el.textContent).includes(target));
            if (!match) return false;
            match.click();
            return true;
        }""",
        normalized,
    )
    if clicked:
        return
    raise FlowRestartRequired(f"Could not find location/section '{text}'")


async def _select_category_count(page: Page, category: str) -> None:
    category_input = page.locator(f"input.cnc-item[data-tevis-cncname='{category}']").first
    if await category_input.count():
        cnc_id = await category_input.get_attribute("data-tevis-cncid")
        if cnc_id:
            plus = page.locator(f"#button-plus-{cnc_id}").first
            if await plus.count():
                await plus.click()
                return

    plus_by_label = page.locator(
        f"button[aria-label*='Erhöhen'][aria-label*='{category}'], "
        f"button[title*='Erhöhen'][title*='{category}']"
    ).first
    if await plus_by_label.count():
        await plus_by_label.click()
        return

    label = page.locator(f"label:has-text('{category}'), div:has-text('{category}')").first
    if await label.count():
        container = label.locator("xpath=ancestor-or-self::*[self::li or self::div][1]")
        plus = container.locator("button[data-type='plus'], button:has(.glyphicon-plus)").first
        if await plus.count():
            await plus.click()
            return

    raise FlowRestartRequired(f"Could not select category '{category}'")


async def _click_ok_if_present(page: Page) -> None:
    button = page.locator("#OKButton, button:has-text('OK')").first
    if await button.count() and await button.is_visible():
        await button.click()
        await page.wait_for_timeout(500)


async def _ensure_calendar_page(page: Page) -> None:
    body = (await page.locator("body").inner_text()).lower()
    if "ihre sitzung läuft" in body and "schließen sie bitte" in body:
        raise FlowRestartRequired("Session expiry warning detected")
    if "schritt 4" not in body and "terminauswahl" not in body and "terminvorschläge" not in body:
        raise FlowRestartRequired("Calendar page is no longer active")


async def _click_first_visible(page: Page, selectors: list[str]) -> None:
    for selector in selectors:
        locator = page.locator(selector).first
        if await locator.count() and await locator.is_visible():
            await locator.click()
            return
    raise FlowRestartRequired(f"Could not click any selector: {selectors}")


def _normalize_selector_text(value: str) -> str:
    import re
    import unicodedata

    without_marks = "".join(
        char
        for char in unicodedata.normalize("NFD", value.lower())
        if unicodedata.category(char) != "Mn"
    )
    return re.sub(r"[^a-z0-9]+", " ", without_marks).strip()


async def _maybe_send_heartbeat(
    config: AppConfig,
    stats: MonitoringStats,
    logger: Logger,
    last_heartbeat_sent: datetime,
) -> bool:
    if not config.heartbeat_enabled or not _heartbeat_due(config, last_heartbeat_sent):
        return False
    last_check = (
        stats.last_successful_calendar_load.isoformat(timespec="seconds")
        if stats.last_successful_calendar_load
        else "never"
    )
    try:
        send_heartbeat_email(
            last_calendar_check=last_check,
            current_retry_interval_seconds=stats.current_wait_seconds,
            dry_run=config.dry_run,
        )
        logger.info("heartbeat_email_sent | last_successful_calendar_check=%s", last_check)
        return True
    except Exception as exc:
        logger.warning("heartbeat_email_failed | %s", exc)
        return False


def _heartbeat_due(config: AppConfig, last_heartbeat_sent: datetime) -> bool:
    return datetime.now() - last_heartbeat_sent >= timedelta(
        minutes=config.heartbeat_interval_minutes
    )


def _record_open_month_observations(
    stats: MonitoringStats,
    observations: list | None,
    config: AppConfig,
    logger: Logger,
) -> None:
    for observation in observations or []:
        if observation.status != "slots_available":
            continue
        first_seen = observation.month not in stats.open_months_seen
        stats.open_months_seen.add(observation.month)
        if first_seen and not observation.target_month:
            logger.info(
                "non_target_slot_month_seen | month=%s | booking_restricted_to=%s | detail='%s'",
                observation.month,
                config.target_month,
                observation.detail or "",
            )
