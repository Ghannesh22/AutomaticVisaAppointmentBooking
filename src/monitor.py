from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from logging import Logger

from playwright.async_api import Page
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from src.booker import SlotSelectionUnavailable, book_slot
from src.config_loader import AppConfig, ApplicantProfile
from src.email_notifier import send_heartbeat_email
from src.logger import safe_filename
from src.manual_challenge import wait_for_manual_captcha_if_present
from src.page_utils import click_locator_with_retry, wait_for_page_ready
from src.phone_alerts import clear_error_alert, create_error_alert
from src.slot_checker import SlotCandidate, check_target_slot
from src.state import has_success_flag, success_flag_path
from src.telegram_notifier import refresh_telegram_subscribers, send_telegram_alert


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

    if _all_profiles_completed(config):
        stats.state = "all_success_flags_present"
        logger.warning(
            "Success flags exist for all applicant profiles; stopping before monitoring to prevent duplicate booking",
        )
        logger.info(stats.summary())
        return True

    deadline = _daily_cutoff_deadline(config.stop_at_time)
    logger.info("Daily stop time: %s", deadline.isoformat(timespec="minutes"))
    attempt = 1
    failure_count = 0
    base_backoff_seconds = min(60, max(5, config.check_interval_seconds // 4))
    max_backoff_seconds = max(config.check_interval_seconds, base_backoff_seconds)

    try:
        while datetime.now() < deadline:
            refresh_telegram_subscribers(logger=logger)
            if _all_profiles_completed(config):
                stats.state = "all_success_flags_present"
                logger.warning(
                    "Success flags exist for all applicant profiles; stopping monitor to prevent duplicate booking",
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
                    clear_error_alert()
                failure_count = 0
                stats.current_retry_count = 0
                stats.current_wait_seconds = config.check_interval_seconds

                while datetime.now() < deadline:
                    refresh_telegram_subscribers(logger=logger)
                    completed_profiles = _completed_profile_names(config)
                    if len(completed_profiles) == len(config.applicant_profiles):
                        stats.state = "all_success_flags_present"
                        logger.warning(
                            "Success flags exist for all applicant profiles; stopping monitor to prevent duplicate booking",
                        )
                        return True
                    active_target_months = config.target_months_for_open_profiles(completed_profiles)

                    stats.state = "checking_calendar"
                    await wait_for_manual_captcha_if_present(page, logger, "calendar monitoring")
                    await _ensure_calendar_page(page)
                    stats.last_successful_calendar_load = datetime.now()
                    result = await check_target_slot(page, config, logger, active_target_months)
                    stats.total_checks += 1
                    _record_open_month_observations(stats, result.month_observations, active_target_months, logger)
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
                        applicant_profile = config.profile_for_month(result.slot.date_text[:7])
                        if has_success_flag(applicant_profile.name):
                            logger.warning(
                                "Slot matched already-booked applicant profile '%s' at %s; continuing monitor",
                                applicant_profile.name,
                                success_flag_path(applicant_profile.name),
                            )
                            await page.reload(wait_until="domcontentloaded")
                            await wait_for_page_ready(page, logger, "calendar reload after completed profile slot")
                            continue
                        stats.state = "slot_detected"
                        logger.info(
                            "slot_detection_result | valid target-month slot detected | applicant_profile=%s | target_months=%s",
                            applicant_profile.name,
                            ",".join(applicant_profile.target_months),
                        )
                        slot_screenshot = await _save_slot_found_screenshot(page, logger)
                        _send_slot_found_telegram_alert(
                            config,
                            applicant_profile,
                            result.slot,
                            page.url,
                            slot_screenshot,
                            logger,
                        )
                        try:
                            success = await book_slot(page, config, result.slot, logger, applicant_profile)
                        except SlotSelectionUnavailable as exc:
                            stats.total_failures += 1
                            saved_screenshot = await _save_error_screenshot(
                                page,
                                logger,
                                f"Slot selection failed: {exc}",
                            )
                            logger.warning(
                                "slot_selection_unavailable | %s | screenshot=%s | continuing monitor",
                                exc,
                                saved_screenshot or "unavailable",
                            )
                            create_error_alert(
                                title="Bot could not select a detected slot",
                                message=str(exc),
                                severity="recoverable",
                                screenshot_path=saved_screenshot,
                            )
                            if send_telegram_alert(
                                title="Bot could not select a detected slot",
                                message=str(exc),
                                severity="recoverable",
                                screenshot_path=saved_screenshot,
                            ):
                                logger.info("Telegram slot-selection error alert sent")
                            stats.state = "waiting"
                            await page.reload(wait_until="domcontentloaded")
                            await wait_for_page_ready(page, logger, "calendar reload after unavailable slot")
                            continue
                        if success:
                            stats.state = "booking_submitted"
                            completed_profiles = _completed_profile_names(config)
                            if len(completed_profiles) == len(config.applicant_profiles):
                                return True
                            logger.info(
                                "Booking completed for applicant profile '%s'; continuing monitor for remaining profiles: %s",
                                applicant_profile.name,
                                ", ".join(
                                    profile.name
                                    for profile in config.applicant_profiles
                                    if profile.name not in completed_profiles
                                ),
                            )
                            break
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
                    if not await _sleep_before_next_action(config.check_interval_seconds, deadline):
                        break
                    await page.reload(wait_until="domcontentloaded")
                    await wait_for_page_ready(page, logger, "calendar reload")
            except ValueError as exc:
                stats.state = "fatal_error"
                stats.total_failures += 1
                saved_screenshot = await _save_error_screenshot(
                    page,
                    logger,
                    f"Configuration or form-fill error: {exc}",
                )
                create_error_alert(
                    title="Bot stopped because of an error",
                    message=str(exc),
                    severity="fatal",
                    screenshot_path=saved_screenshot,
                )
                if send_telegram_alert(
                    title="Bot stopped because of an error",
                    message=str(exc),
                    severity="fatal",
                    screenshot_path=saved_screenshot,
                    force=True,
                ):
                    logger.info("Telegram fatal error alert sent")
                return False
            except (FlowRestartRequired, PlaywrightTimeoutError, PlaywrightError) as exc:
                stats.state = "navigation_failure"
                stats.total_failures += 1
                saved_screenshot = await _save_error_screenshot(
                    page,
                    logger,
                    f"Flow failed: {exc}",
                )
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
                create_error_alert(
                    title="Bot hit an error and is retrying",
                    message=str(exc).splitlines()[0][:500],
                    severity="recoverable",
                    screenshot_path=saved_screenshot,
                    retry_count=failure_count,
                    next_wait_seconds=backoff_seconds,
                )
                if send_telegram_alert(
                    title="Bot hit an error and is retrying",
                    message=(
                        f"{str(exc).splitlines()[0][:500]}\n\n"
                        f"Retry count: {failure_count}\n"
                        f"Next retry wait: {backoff_seconds} seconds"
                    ),
                    severity="recoverable",
                    screenshot_path=saved_screenshot,
                ):
                    logger.info("Telegram recoverable error alert sent")
                if await _maybe_send_heartbeat(config, stats, logger, last_heartbeat_sent):
                    last_heartbeat_sent = datetime.now()
                if not await _sleep_before_next_action(backoff_seconds, deadline):
                    break
                continue
            except Exception as exc:
                stats.state = "fatal_error"
                stats.total_failures += 1
                saved_screenshot = await _save_error_screenshot(
                    page,
                    logger,
                    f"Unexpected monitor error: {exc}",
                )
                create_error_alert(
                    title="Bot stopped because of an unexpected error",
                    message=str(exc),
                    severity="fatal",
                    screenshot_path=saved_screenshot,
                )
                if send_telegram_alert(
                    title="Bot stopped because of an unexpected error",
                    message=str(exc),
                    severity="fatal",
                    screenshot_path=saved_screenshot,
                    force=True,
                ):
                    logger.info("Telegram unexpected error alert sent")
                return False

        stats.state = "daily_stop_time_reached"
        logger.info("Daily stop time reached without a successful booking")
        return False
    except asyncio.CancelledError:
        stats.state = "cancelled"
        logger.info("Shutdown requested; stopping monitor cleanly")
        raise
    finally:
        logger.info(stats.summary())


async def navigate_to_calendar(page: Page, config: AppConfig, logger: Logger) -> None:
    logger.info("Opening website: %s", config.website_url)
    await page.goto(config.website_url, wait_until="domcontentloaded")
    await wait_for_page_ready(page, logger, "initial page load")
    await _accept_cookies_if_present(page, logger)

    logger.info("Step 1: selecting Ausländer- und Staatsangehörigkeitsbehörde")
    await _click_first_visible(
        page,
        [
            "#buttonfunktionseinheit-1",
            "button[name='Ausländer- und Staatsangehörigkeitsbehörde']",
            "button:has-text('Ausländer- und Staatsangehörigkeitsbehörde')",
        ],
        logger,
        "function unit",
    )

    logger.info("Step 2: selecting location %s", config.appointment_location)
    await _choose_location_or_section(page, config.appointment_location, logger)

    logger.info("Step 2: selecting category %s and applicant count 1", config.visa_category)
    await _select_category_count(page, config.visa_category, logger)

    logger.info("Step 2: clicking Weiter")
    await _click_first_visible(page, ["#WeiterButton", "input[value='Weiter']", "button:has-text('Weiter')"], logger, "Step 2 Weiter")

    logger.info("Step 2 popup: accepting info dialog with OK")
    await _click_ok_if_present(page, logger)

    logger.info("Step 3: continuing past location/address page")
    await _click_first_visible(page, ["#WeiterButton", "input[value='Weiter']", "button:has-text('Weiter')"], logger, "Step 3 Weiter")

    await _ensure_calendar_page(page)
    logger.info("Step 4: reached appointment calendar/slot page")


async def _accept_cookies_if_present(page: Page, logger: Logger) -> None:
    button = page.locator("#cookie_msg_btn_yes, input[value='Akzeptieren'], button:has-text('Akzeptieren')").first
    if await button.count() and await button.is_visible():
        logger.info("Accepting cookie banner")
        await click_locator_with_retry(button, page, logger, "cookie accept")


async def _choose_location_or_section(page: Page, text: str, logger: Logger) -> None:
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
            await click_locator_with_retry(locator, page, logger, f"location/section {text}")
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
        await wait_for_page_ready(page, logger, f"location/section {text}")
        return
    raise FlowRestartRequired(f"Could not find location/section '{text}'")


async def _select_category_count(page: Page, category: str, logger: Logger) -> None:
    category_input = page.locator(f"input.cnc-item[data-tevis-cncname='{category}']").first
    if await category_input.count():
        cnc_id = await category_input.get_attribute("data-tevis-cncid")
        if cnc_id:
            plus = page.locator(f"#button-plus-{cnc_id}").first
            if await plus.count():
                await click_locator_with_retry(plus, page, logger, f"category count {category}")
                return

    plus_by_label = page.locator(
        f"button[aria-label*='Erhöhen'][aria-label*='{category}'], "
        f"button[title*='Erhöhen'][title*='{category}']"
    ).first
    if await plus_by_label.count():
        await click_locator_with_retry(plus_by_label, page, logger, f"category count {category}")
        return

    label = page.locator(f"label:has-text('{category}'), div:has-text('{category}')").first
    if await label.count():
        container = label.locator("xpath=ancestor-or-self::*[self::li or self::div][1]")
        plus = container.locator("button[data-type='plus'], button:has(.glyphicon-plus)").first
        if await plus.count():
            await click_locator_with_retry(plus, page, logger, f"category count {category}")
            return

    raise FlowRestartRequired(f"Could not select category '{category}'")


async def _click_ok_if_present(page: Page, logger: Logger) -> None:
    button = page.locator("#OKButton, button:has-text('OK')").first
    if await button.count() and await button.is_visible():
        await click_locator_with_retry(button, page, logger, "OK dialog")


async def _ensure_calendar_page(page: Page) -> None:
    body = (await page.locator("body").inner_text()).lower()
    if "ihre sitzung läuft" in body and "schließen sie bitte" in body:
        raise FlowRestartRequired("Session expiry warning detected")
    if "schritt 4" not in body and "terminauswahl" not in body and "terminvorschläge" not in body:
        raise FlowRestartRequired("Calendar page is no longer active")


async def _click_first_visible(page: Page, selectors: list[str], logger: Logger, description: str) -> None:
    for selector in selectors:
        locator = page.locator(selector).first
        if await locator.count() and await locator.is_visible():
            await click_locator_with_retry(locator, page, logger, description)
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


async def _save_error_screenshot(page: Page, logger: Logger, message: str):
    screenshot = safe_filename("error")
    try:
        await page.screenshot(path=str(screenshot), full_page=True)
    except Exception as exc:
        logger.error("%s before screenshot could be saved: %s", message, exc)
        return None

    logger.error("%s. Saved screenshot: %s", message, screenshot)
    return screenshot


async def _save_slot_found_screenshot(page: Page, logger: Logger):
    screenshot = safe_filename("slot_found")
    try:
        await page.screenshot(path=str(screenshot), full_page=True)
    except Exception as exc:
        logger.warning("Slot detected before screenshot could be saved: %s", exc)
        return None

    logger.info("Saved slot-detected screenshot: %s", screenshot)
    return screenshot


def _send_slot_found_telegram_alert(
    config: AppConfig,
    applicant_profile: ApplicantProfile,
    slot: SlotCandidate,
    session_url: str,
    screenshot_path,
    logger: Logger,
) -> None:
    fresh_start_url = config.website_url.strip()
    session_url = session_url.strip()
    control_page_url = os.getenv("CONTROL_PAGE_URL", "").strip()
    detected_at = datetime.now().isoformat(timespec="seconds")
    lines = [
        "Slot detected now.",
        "",
        "Important: the Step 4 calendar URL is tied to the laptop browser session. "
        "Opening it from Telegram or another browser can show 'No valid location found'.",
        "",
        "Use the already-open laptop browser or remote desktop to click the slot, or let the bot continue.",
        f"Fresh website start URL: {fresh_start_url}",
    ]
    if control_page_url:
        lines.append(f"Phone control page: {control_page_url}")
    if session_url and session_url.rstrip("/") != fresh_start_url.rstrip("/"):
        lines.append(f"Laptop session URL when detected: {session_url}")
    lines.extend(
        [
            "",
            f"Detected at: {detected_at}",
            f"Slot date: {slot.date_text}",
            f"Slot time: {slot.time_text}",
            f"Location: {config.appointment_location}",
            f"Applicant profile: {applicant_profile.name}",
            f"Target months: {', '.join(applicant_profile.target_months)}",
            f"Dry run: {str(config.dry_run).lower()}",
            "",
            f"Visible slot text: {slot.raw_text[:500]}",
        ]
    )
    message = "\n".join(lines)
    if send_telegram_alert(
        title="Appointment slot found",
        message=message,
        severity="urgent",
        screenshot_path=screenshot_path,
        force=True,
    ):
        logger.info("Telegram slot-found alert sent")


def _heartbeat_due(config: AppConfig, last_heartbeat_sent: datetime) -> bool:
    return datetime.now() - last_heartbeat_sent >= timedelta(
        minutes=config.heartbeat_interval_minutes
    )


def _daily_cutoff_deadline(stop_at_time: time) -> datetime:
    now = datetime.now()
    return datetime.combine(now.date(), stop_at_time)


def _seconds_until(deadline: datetime) -> float:
    return max(0.0, (deadline - datetime.now()).total_seconds())


async def _sleep_before_next_action(wait_seconds: int, deadline: datetime) -> bool:
    remaining_seconds = _seconds_until(deadline)
    if remaining_seconds <= 0:
        return False
    await asyncio.sleep(min(wait_seconds, remaining_seconds))
    return datetime.now() < deadline


def _record_open_month_observations(
    stats: MonitoringStats,
    observations: list | None,
    active_target_months: tuple[str, ...],
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
                ",".join(active_target_months),
                observation.detail or "",
            )


def _completed_profile_names(config: AppConfig) -> set[str]:
    return {
        profile.name
        for profile in config.applicant_profiles
        if has_success_flag(profile.name)
    }


def _all_profiles_completed(config: AppConfig) -> bool:
    return len(_completed_profile_names(config)) == len(config.applicant_profiles)
