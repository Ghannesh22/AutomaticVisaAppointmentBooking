from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from src.config_loader import AppConfig


@asynccontextmanager
async def browser_page(config: AppConfig) -> AsyncIterator[Page]:
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(
            headless=config.headless,
            slow_mo=150 if not config.headless else 0,
        )
        context: BrowserContext = await browser.new_context(
            locale="de-DE",
            timezone_id="Europe/Berlin",
            viewport={"width": 1366, "height": 900},
        )
        page = await context.new_page()
        page.set_default_timeout(config.browser_timeout_seconds * 1000)
        try:
            yield page
        finally:
            await context.close()
            await browser.close()
