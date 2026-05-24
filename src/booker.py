from __future__ import annotations

import asyncio
from logging import Logger
from pathlib import Path

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from src.config_loader import AppConfig, PROJECT_ROOT
from src.email_notifier import send_booking_email, smtp_enabled
from src.form_filler import fill_personal_details
from src.logger import safe_filename
from src.page_utils import click_element_with_retry, click_locator_with_retry, wait_for_page_ready
from src.slot_checker import SlotCandidate
from src.state import has_success_flag, success_flag_path, write_success_flag


async def book_slot(page: Page, config: AppConfig, slot: SlotCandidate, logger: Logger) -> bool:
    if has_success_flag():
        logger.warning(
            "Success flag already exists at %s; refusing to select or submit another booking",
            success_flag_path(),
        )
        return True

    logger.info("Selecting candidate slot: %s", slot.raw_text)
    await click_element_with_retry(slot.element, page, logger, "appointment time")

    await _advance_after_slot_selection(page, logger)
    await fill_personal_details(page, logger)
    await _click_continue(page, logger)

    if config.dry_run:
        logger.info("Dry-run mode is enabled; stopping before final booking submission")
        return False

    if has_success_flag():
        logger.warning(
            "Success flag appeared before final submit at %s; refusing duplicate submission",
            success_flag_path(),
        )
        return True

    logger.info("Dry-run disabled; submitting final booking")
    await _click_final_submit(page, logger)
    await wait_for_page_ready(page, logger, "final booking submit", settle_ms=1500)

    screenshot = safe_filename("booking_success")
    await page.screenshot(path=str(screenshot), full_page=True)
    confirmation_text = await page.locator("body").inner_text()
    confirmation_file = _write_confirmation(confirmation_text)
    reference = _extract_reference(confirmation_text)

    logger.info("Saved success screenshot to %s", screenshot)
    logger.info("Saved confirmation text to %s", confirmation_file)
    flag = write_success_flag(
        appointment_date=slot.date_text,
        appointment_time=slot.time_text,
        location=config.appointment_location,
        reference=reference,
    )
    logger.info("Created booking success flag at %s", flag)
    if smtp_enabled():
        send_booking_email(
            appointment_date=slot.date_text,
            appointment_time=slot.time_text,
            location=config.appointment_location,
            reference=reference,
            attachment=screenshot,
        )
        logger.info("Sent Gmail SMTP booking notification")
    else:
        logger.info("SMTP settings are empty; skipping email notification")
    return True


async def _click_continue(page: Page, logger: Logger) -> None:
    logger.info("Clicking Weiter")
    button = _continue_button(page)
    await click_locator_with_retry(button, page, logger, "Weiter", click_timeout_ms=10000)


async def _advance_after_slot_selection(page: Page, logger: Logger) -> None:
    for _ in range(20):
        if await _has_visible_applicant_fields(page):
            logger.info("Slot selection reached applicant details form automatically")
            return

        if await _click_slot_confirmation_yes_if_present(page, logger):
            continue

        button = _continue_button(page)
        if await button.count() and await button.is_visible():
            await _click_continue(page, logger)
            return

        await asyncio.sleep(0.25)

    raise PlaywrightTimeoutError(
        "After selecting an appointment time, neither applicant fields nor Weiter became visible"
    )


async def _has_visible_applicant_fields(page: Page) -> bool:
    fields = page.locator(
        "input:not([type=hidden]):not([type=submit]):not([type=button]), textarea, select"
    )
    count = await fields.count()
    for index in range(count):
        if await fields.nth(index).is_visible():
            return True
    return False


def _continue_button(page: Page):
    return page.locator(
        "#WeiterButton, input[value='Weiter'], button:has-text('Weiter'), "
        "input[type=submit][title='Weiter']"
    ).first


async def _click_slot_confirmation_yes_if_present(page: Page, logger: Logger) -> bool:
    try:
        body_text = await page.locator("body").inner_text(timeout=2000)
    except PlaywrightTimeoutError:
        return False
    normalized = body_text.lower().replace("ö", "oe")
    if "mit diesen eingaben fortfahren" not in normalized:
        return False

    yes_button = page.locator(
        "input[value='Ja'], input[value='Yes'], input[type=submit][value*='Ja'], "
        "button:has-text('Ja'), button:has-text('Yes'), a:has-text('Ja'), a:has-text('Yes')"
    ).first
    if not await yes_button.count() or not await yes_button.is_visible():
        return False

    logger.info("Confirming selected appointment time with Ja")
    await click_locator_with_retry(yes_button, page, logger, "appointment confirmation Ja", click_timeout_ms=10000)
    return True


async def _click_final_submit(page: Page, logger: Logger) -> None:
    logger.info("Clicking final Book now/booking button")
    button = page.locator(
        "input[value*='Book now'], input[value*='Jetzt buchen'], "
        "input[value*='Reservieren'], input[value*='Buchen'], input[value*='Absenden'], "
        "button:has-text('Book now'), button:has-text('Jetzt buchen'), "
        "button:has-text('Reservieren'), button:has-text('Buchen'), button:has-text('Absenden'), "
        "a:has-text('Book now'), a:has-text('Jetzt buchen'), "
        "#WeiterButton, input[value='Weiter']"
    ).first
    await click_locator_with_retry(button, page, logger, "final Book now/booking button", click_timeout_ms=10000)


def _write_confirmation(text: str) -> Path:
    from datetime import datetime

    directory = PROJECT_ROOT / "confirmations"
    directory.mkdir(exist_ok=True)
    path = directory / f"confirmation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    path.write_text(text, encoding="utf-8")
    return path


def _extract_reference(text: str) -> str | None:
    import re

    match = re.search(
        r"(?:Referenz|Vorgangsnummer|Bestätigungsnummer|Reservierungsnummer)\s*[:#]?\s*([A-Z0-9-]+)",
        text,
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else None
