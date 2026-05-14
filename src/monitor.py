from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from logging import Logger

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from src.booker import book_slot
from src.config_loader import AppConfig
from src.logger import safe_filename
from src.slot_checker import find_july_slot


class FlowRestartRequired(RuntimeError):
    pass


async def run_monitor(page: Page, config: AppConfig, logger: Logger) -> bool:
    deadline = datetime.now() + timedelta(minutes=config.max_runtime_minutes)
    attempt = 1

    while datetime.now() < deadline:
        logger.info("Starting booking flow attempt %s", attempt)
        try:
            await navigate_to_calendar(page, config, logger)
            while datetime.now() < deadline:
                await _ensure_calendar_page(page)
                slot = await find_july_slot(page, config, logger)
                if slot:
                    success = await book_slot(page, config, slot, logger)
                    if success:
                        return True
                    logger.info("Dry-run reached booking boundary; stopping monitor")
                    return False

                logger.info(
                    "Waiting %s seconds before checking again",
                    config.check_interval_seconds,
                )
                await asyncio.sleep(config.check_interval_seconds)
                await page.reload(wait_until="networkidle")
        except (FlowRestartRequired, PlaywrightTimeoutError, ValueError) as exc:
            screenshot = safe_filename("error")
            try:
                await page.screenshot(path=str(screenshot), full_page=True)
                logger.error("Flow failed: %s. Saved screenshot: %s", exc, screenshot)
            except Exception:
                logger.error("Flow failed before screenshot could be saved: %s", exc)
            attempt += 1
            await asyncio.sleep(5)
            continue

    logger.info("Max runtime reached without a successful booking")
    return False


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
