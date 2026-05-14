from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from logging import Logger

from playwright.async_api import ElementHandle, Page

from src.config_loader import AppConfig


MONTHS_DE = {
    "januar": 1,
    "februar": 2,
    "märz": 3,
    "maerz": 3,
    "april": 4,
    "mai": 5,
    "juni": 6,
    "juli": 7,
    "august": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "dezember": 12,
}


@dataclass
class SlotCandidate:
    element: ElementHandle
    date_text: str
    time_text: str
    raw_text: str


async def find_july_slot(page: Page, config: AppConfig, logger: Logger) -> SlotCandidate | None:
    await _move_calendar_to_target_month(page, config, logger)

    handles = await page.locator(
        "a, button, input[type=button], input[type=submit], td[role=gridcell], "
        "[role=button], .ui-state-default, .ui-datepicker-calendar td"
    ).element_handles()

    for handle in handles:
        if not await _is_candidate_enabled(handle):
            continue
        text = await _element_text(handle)
        parsed_date = _extract_date(text, config)
        if not parsed_date:
            continue
        if parsed_date.year != config.target_year or parsed_date.month != config.target_month_number:
            continue

        time_text = _extract_time(text) or "Unknown"
        logger.info("Found possible July 2026 appointment: %s | %s", parsed_date.isoformat(), text)
        return SlotCandidate(
            element=handle,
            date_text=parsed_date.isoformat(),
            time_text=time_text,
            raw_text=text,
        )

    logger.info("No visible July 2026 appointment slot found on current calendar page")
    return None


async def _move_calendar_to_target_month(page: Page, config: AppConfig, logger: Logger) -> None:
    for _ in range(30):
        page_text = (await page.locator("body").inner_text()).lower()
        if _target_month_visible(page_text, config):
            logger.info("Calendar/page text contains target month %s", config.target_month)
            return

        next_button = page.locator(
            "a[title*='Weiter'], a[aria-label*='Weiter'], "
            "button[title*='Weiter'], button[aria-label*='Weiter'], "
            ".ui-datepicker-next, [data-handler='next']"
        ).first
        if await next_button.count() == 0:
            return
        if not await next_button.is_visible():
            return

        logger.info("Target month not visible yet; clicking next-month control")
        await next_button.click()
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(500)


def _target_month_visible(text: str, config: AppConfig) -> bool:
    month_name = next(
        name
        for name, number in MONTHS_DE.items()
        if number == config.target_month_number and "ae" not in name
    )
    return month_name in text and str(config.target_year) in text


async def _is_candidate_enabled(handle: ElementHandle) -> bool:
    return await handle.evaluate(
        """el => {
            const style = window.getComputedStyle(el);
            const disabled = el.disabled || el.getAttribute('aria-disabled') === 'true';
            const unavailable = /disabled|unavailable|nicht|belegt|rot/i.test(el.className || '');
            return !disabled && !unavailable && style.visibility !== 'hidden' && style.display !== 'none';
        }"""
    )


async def _element_text(handle: ElementHandle) -> str:
    return await handle.evaluate(
        """el => [
            el.innerText,
            el.textContent,
            el.value,
            el.getAttribute('aria-label'),
            el.getAttribute('title')
        ].filter(Boolean).join(' ').replace(/\\s+/g, ' ').trim()"""
    )


def _extract_date(text: str, config: AppConfig) -> date | None:
    normalized = text.lower().replace(",", " ")

    for day, month, year in re.findall(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b", normalized):
        return _safe_date(int(year), int(month), int(day))

    for year, month, day in re.findall(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", normalized):
        return _safe_date(int(year), int(month), int(day))

    for day, month_name, year in re.findall(
        r"\b(\d{1,2})\.\s*([a-zäöü]+)\s+(\d{4})\b",
        normalized,
    ):
        month = MONTHS_DE.get(month_name)
        if month:
            return _safe_date(int(year), month, int(day))

    calendar_day = re.fullmatch(r"\s*(\d{1,2})\s*", normalized)
    if calendar_day and _target_month_visible(normalized, config):
        return _safe_date(config.target_year, config.target_month_number, int(calendar_day.group(1)))

    return None


def _extract_time(text: str) -> str | None:
    match = re.search(r"\b([01]?\d|2[0-3])[:.][0-5]\d\b", text)
    return match.group(0).replace(".", ":") if match else None


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None
