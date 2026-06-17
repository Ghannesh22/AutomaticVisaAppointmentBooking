from __future__ import annotations

import time
from logging import Logger

from playwright.async_api import Page

from src.config_loader import PROJECT_ROOT
from src.manual_action_state import (
    MANUAL_ACTION_IMAGE_PATH,
    clear_manual_action_request,
    create_manual_action_request,
)
from src.telegram_notifier import send_telegram_alert


MANUAL_CHALLENGE_TIMEOUT_SECONDS = 900


async def wait_for_manual_captcha_if_present(page: Page, logger: Logger, context: str) -> bool:
    await _install_manual_captcha_enter_listener(page)
    if not await _manual_captcha_visible(page):
        if await _manual_captcha_enter_pressed(page):
            logger.info("Manual CAPTCHA completion confirmed by Enter key during %s", context)
            return True
        return False

    logger.warning(
        "Manual CAPTCHA challenge detected during %s. Complete it manually in the laptop browser; "
        "then press Enter in the browser to let the bot continue.",
        context,
    )
    screenshot = await _save_manual_action_screenshot(page, logger)
    request_id = create_manual_action_request(
        title="Manual CAPTCHA required",
        message=(
            "The laptop browser is waiting on an 'I am not a robot' challenge. "
            "Open Chrome Remote Desktop or another remote desktop app on your phone, control the laptop browser, "
            "complete the challenge manually, then press Enter in the browser to let the bot continue."
        ),
        image_path=screenshot,
    )
    if send_telegram_alert(
        title="Manual CAPTCHA required",
        message=(
            "The laptop bot is waiting on an 'I am not a robot' challenge. "
            "Open Chrome Remote Desktop or another remote desktop app on your phone, control the laptop browser, "
            "complete it manually, then press Enter in the browser to let the bot continue."
        ),
        severity="manual_action",
        screenshot_path=screenshot,
        force=True,
    ):
        logger.info("Telegram CAPTCHA alert sent")
    deadline = time.monotonic() + MANUAL_CHALLENGE_TIMEOUT_SECONDS
    try:
        while time.monotonic() < deadline:
            if await _manual_captcha_enter_pressed(page):
                logger.info("Manual CAPTCHA completion confirmed by Enter key; continuing booking flow")
                await page.wait_for_timeout(500)
                return True
            if not await _manual_captcha_visible(page):
                logger.info("Manual CAPTCHA challenge cleared; continuing booking flow")
                return True
            await page.wait_for_timeout(1000)
        raise ValueError("Timed out waiting for manual CAPTCHA challenge completion")
    finally:
        clear_manual_action_request(request_id)


async def _install_manual_captcha_enter_listener(page: Page) -> None:
    try:
        await page.evaluate(
            """() => {
                if (window.__visaBotManualCaptchaEnterListenerInstalled) {
                    return;
                }
                window.__visaBotManualCaptchaEnterListenerInstalled = true;
                window.__visaBotManualCaptchaEnterPressed = false;
                document.addEventListener("keydown", event => {
                    if (event.key === "Enter") {
                        window.__visaBotManualCaptchaEnterPressed = true;
                    }
                }, true);
            }"""
        )
    except Exception:
        return


async def _manual_captcha_enter_pressed(page: Page) -> bool:
    try:
        return bool(
            await page.evaluate(
                """() => {
                    const pressed = Boolean(window.__visaBotManualCaptchaEnterPressed);
                    window.__visaBotManualCaptchaEnterPressed = false;
                    return pressed;
                }"""
            )
        )
    except Exception:
        return False


async def _manual_captcha_visible(page: Page) -> bool:
    try:
        return bool(
            await page.evaluate(
                """() => {
                    const bodyText = (document.body && document.body.innerText || '').toLowerCase();
                    const frameText = Array.from(document.querySelectorAll('iframe'))
                        .map(frame => [
                            frame.src || '',
                            frame.title || '',
                            frame.name || '',
                            frame.getAttribute('aria-label') || ''
                        ].join(' '))
                        .join(' ')
                        .toLowerCase();
                    const combined = `${bodyText} ${frameText}`;
                    return /i\\s*am\\s*not\\s*a\\s*robot|i'm\\s*not\\s*a\\s*robot|ich\\s*bin\\s*kein\\s*roboter|not\\s*a\\s*robot|recaptcha|hcaptcha|captcha/.test(combined);
                }"""
            )
        )
    except Exception:
        return False


async def _save_manual_action_screenshot(page: Page, logger: Logger):
    try:
        MANUAL_ACTION_IMAGE_PATH.parent.mkdir(exist_ok=True)
        await page.screenshot(path=str(MANUAL_ACTION_IMAGE_PATH), full_page=True)
        return MANUAL_ACTION_IMAGE_PATH
    except Exception as exc:
        logger.warning("Could not save manual CAPTCHA screenshot for phone: %s", exc)
        return None
