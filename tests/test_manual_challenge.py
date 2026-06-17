import asyncio
import logging
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src.manual_challenge import wait_for_manual_captcha_if_present


class ManualCaptchaTelegramAlertTests(unittest.TestCase):
    def test_manual_captcha_alert_is_forced_and_includes_screenshot(self):
        logger = logging.getLogger("test_manual_captcha_alert")
        logger.handlers = [logging.NullHandler()]
        logger.propagate = False

        with (
            patch("src.manual_challenge._manual_captcha_visible", new_callable=AsyncMock) as visible,
            patch("src.manual_challenge._save_manual_action_screenshot", new_callable=AsyncMock) as save_screenshot,
            patch("src.manual_challenge.create_manual_action_request", return_value="captcha-1"),
            patch("src.manual_challenge.clear_manual_action_request") as clear_request,
            patch("src.manual_challenge.send_telegram_alert", return_value=True) as send_alert,
        ):
            visible.side_effect = [True, False]
            save_screenshot.return_value = Path("logs/manual_action.png")

            asyncio.run(wait_for_manual_captcha_if_present(object(), logger, "test"))

        send_alert.assert_called_once()
        kwargs = send_alert.call_args.kwargs
        self.assertEqual(kwargs["title"], "Manual CAPTCHA required")
        self.assertEqual(kwargs["severity"], "manual_action")
        self.assertEqual(kwargs["screenshot_path"], Path("logs/manual_action.png"))
        self.assertTrue(kwargs["force"])
        clear_request.assert_called_once_with("captcha-1")

    def test_manual_captcha_continues_after_enter_key_signal(self):
        class FakePage:
            async def wait_for_timeout(self, _milliseconds):
                return None

        logger = logging.getLogger("test_manual_captcha_enter")
        logger.handlers = [logging.NullHandler()]
        logger.propagate = False

        with (
            patch("src.manual_challenge._install_manual_captcha_enter_listener", new_callable=AsyncMock),
            patch("src.manual_challenge._manual_captcha_visible", new_callable=AsyncMock) as visible,
            patch("src.manual_challenge._manual_captcha_enter_pressed", new_callable=AsyncMock) as enter_pressed,
            patch("src.manual_challenge._save_manual_action_screenshot", new_callable=AsyncMock) as save_screenshot,
            patch("src.manual_challenge.create_manual_action_request", return_value="captcha-2"),
            patch("src.manual_challenge.clear_manual_action_request") as clear_request,
            patch("src.manual_challenge.send_telegram_alert", return_value=False),
        ):
            visible.return_value = True
            enter_pressed.return_value = True
            save_screenshot.return_value = Path("logs/manual_action.png")

            handled = asyncio.run(wait_for_manual_captcha_if_present(FakePage(), logger, "test"))

        self.assertTrue(handled)
        clear_request.assert_called_once_with("captcha-2")


if __name__ == "__main__":
    unittest.main()
