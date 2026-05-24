from __future__ import annotations

from logging import Logger

from playwright.async_api import ElementHandle, Locator, Page
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError


async def wait_for_page_ready(
    page: Page,
    logger: Logger,
    context: str,
    *,
    networkidle_timeout_ms: int = 8000,
    domcontent_timeout_ms: int = 5000,
    settle_ms: int = 500,
) -> None:
    try:
        await page.wait_for_load_state("networkidle", timeout=networkidle_timeout_ms)
    except PlaywrightTimeoutError:
        logger.info(
            "page_ready_fallback | context=%s | networkidle_timeout_ms=%s",
            context,
            networkidle_timeout_ms,
        )
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=domcontent_timeout_ms)
        except PlaywrightTimeoutError:
            logger.info(
                "page_ready_fallback | context=%s | domcontentloaded_timeout_ms=%s",
                context,
                domcontent_timeout_ms,
            )
    await page.wait_for_timeout(settle_ms)


async def click_locator_with_retry(
    locator: Locator,
    page: Page,
    logger: Logger,
    description: str,
    *,
    attempts: int = 3,
    click_timeout_ms: int = 8000,
) -> None:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            await locator.click(timeout=click_timeout_ms)
            await wait_for_page_ready(page, logger, description)
            return
        except (PlaywrightTimeoutError, PlaywrightError) as exc:
            last_error = exc
            logger.info(
                "click_retry | target=%s | attempt=%s/%s | error=%s",
                description,
                attempt,
                attempts,
                _short_error(exc),
            )
            await page.wait_for_timeout(500 * attempt)
    if last_error:
        raise last_error


async def click_element_with_retry(
    element: ElementHandle,
    page: Page,
    logger: Logger,
    description: str,
    *,
    attempts: int = 3,
    click_timeout_ms: int = 8000,
) -> None:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            await element.click(timeout=click_timeout_ms)
            await wait_for_page_ready(page, logger, description)
            return
        except (PlaywrightTimeoutError, PlaywrightError) as exc:
            last_error = exc
            logger.info(
                "click_retry | target=%s | attempt=%s/%s | error=%s",
                description,
                attempt,
                attempts,
                _short_error(exc),
            )
            await page.wait_for_timeout(500 * attempt)
    if last_error:
        raise last_error


def _short_error(exc: Exception) -> str:
    return str(exc).splitlines()[0][:220]
