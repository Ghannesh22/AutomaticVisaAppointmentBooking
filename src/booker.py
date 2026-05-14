from __future__ import annotations

from logging import Logger
from pathlib import Path

from playwright.async_api import Page

from src.config_loader import AppConfig, PROJECT_ROOT
from src.email_notifier import send_booking_email, smtp_enabled
from src.form_filler import fill_personal_details
from src.logger import safe_filename
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
    await slot.element.click()
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(1000)

    await _click_continue(page, logger)
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
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(1500)

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
    button = page.locator(
        "#WeiterButton, input[value='Weiter'], button:has-text('Weiter'), "
        "input[type=submit][title='Weiter']"
    ).first
    await button.click()
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(800)


async def _click_final_submit(page: Page, logger: Logger) -> None:
    logger.info("Clicking final booking/reservation button")
    button = page.locator(
        "input[value*='Reservieren'], input[value*='Buchen'], input[value*='Absenden'], "
        "button:has-text('Reservieren'), button:has-text('Buchen'), button:has-text('Absenden'), "
        "#WeiterButton, input[value='Weiter']"
    ).first
    await button.click()


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
