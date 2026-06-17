import asyncio
import logging
import unittest
from datetime import time
from pathlib import Path
from unittest.mock import patch

from src.config_loader import AppConfig, ApplicantProfile
from src.monitor import _save_error_screenshot, _save_slot_found_screenshot, _send_slot_found_telegram_alert
from src.slot_checker import SlotCandidate


class FakePage:
    def __init__(self):
        self.screenshot_path = None
        self.full_page = None

    async def screenshot(self, *, path, full_page):
        self.screenshot_path = path
        self.full_page = full_page


class ErrorScreenshotTests(unittest.TestCase):
    def test_save_error_screenshot_uses_full_page_capture(self):
        page = FakePage()
        logger = logging.getLogger("test_monitor_screenshot")
        logger.handlers = [logging.NullHandler()]
        logger.propagate = False

        saved_path = asyncio.run(
            _save_error_screenshot(page, logger, "test error")
        )

        self.assertIsNotNone(saved_path)
        self.assertEqual(str(saved_path), page.screenshot_path)
        self.assertTrue(page.full_page)
        self.assertTrue(saved_path.name.startswith("error_"))

    def test_save_slot_found_screenshot_uses_full_page_capture(self):
        page = FakePage()
        logger = logging.getLogger("test_monitor_slot_screenshot")
        logger.handlers = [logging.NullHandler()]
        logger.propagate = False

        saved_path = asyncio.run(_save_slot_found_screenshot(page, logger))

        self.assertIsNotNone(saved_path)
        self.assertEqual(str(saved_path), page.screenshot_path)
        self.assertTrue(page.full_page)
        self.assertTrue(saved_path.name.startswith("slot_found_"))


class SlotFoundTelegramAlertTests(unittest.TestCase):
    def test_slot_found_alert_includes_slot_details_and_bypasses_throttle(self):
        config = AppConfig(
            website_url="https://termine.staedteregion-aachen.de/auslaenderamt/",
            visa_category="RWTH Studenten",
            appointment_location="RWTH - Aussenstelle Super C",
            target_months=("2026-07", "2026-08"),
            check_interval_seconds=10,
            stop_at_time=time(17, 0),
            headless=True,
            dry_run=False,
            browser_timeout_seconds=30,
            heartbeat_enabled=False,
            heartbeat_interval_minutes=240,
        )
        slot = SlotCandidate(
            element=None,
            date_text="2026-08-19",
            time_text="08:30",
            raw_text="Mittwoch, 19.08.2026 | 08:30 08:30 08:30",
        )
        logger = logging.getLogger("test_monitor_slot_found")
        logger.handlers = [logging.NullHandler()]
        logger.propagate = False
        profile = ApplicantProfile("jessico", ("2026-07", "2026-08"), "APPLICANT")

        with patch("src.monitor.send_telegram_alert", return_value=True) as send_alert:
            _send_slot_found_telegram_alert(
                config,
                profile,
                slot,
                "https://termine.staedteregion-aachen.de/auslaenderamt/",
                Path("logs/slot_found.png"),
                logger,
            )

        send_alert.assert_called_once()
        kwargs = send_alert.call_args.kwargs
        self.assertEqual(kwargs["title"], "Appointment slot found")
        self.assertEqual(kwargs["severity"], "urgent")
        self.assertEqual(kwargs["screenshot_path"], Path("logs/slot_found.png"))
        self.assertTrue(kwargs["force"])
        self.assertIn("Slot detected now.", kwargs["message"])
        self.assertIn("Step 4 calendar URL is tied to the laptop browser session", kwargs["message"])
        self.assertIn(
            "Fresh website start URL: https://termine.staedteregion-aachen.de/auslaenderamt/",
            kwargs["message"],
        )
        self.assertNotIn("/suggest", kwargs["message"])
        self.assertIn("Detected at:", kwargs["message"])
        self.assertIn("Slot date: 2026-08-19", kwargs["message"])
        self.assertIn("Slot time: 08:30", kwargs["message"])
        self.assertIn("Applicant profile: jessico", kwargs["message"])
        self.assertIn("Target months: 2026-07, 2026-08", kwargs["message"])
        self.assertIn("Dry run: false", kwargs["message"])

    def test_slot_found_alert_falls_back_to_configured_website_url(self):
        config = AppConfig(
            website_url="https://example.test/start",
            visa_category="RWTH Studenten",
            appointment_location="RWTH - Aussenstelle Super C",
            target_months=("2026-07",),
            check_interval_seconds=10,
            stop_at_time=time(17, 0),
            headless=True,
            dry_run=True,
            browser_timeout_seconds=30,
            heartbeat_enabled=False,
            heartbeat_interval_minutes=240,
        )
        slot = SlotCandidate(
            element=None,
            date_text="2026-07-01",
            time_text="09:00",
            raw_text="Dienstag, 01.07.2026 | 09:00",
        )
        logger = logging.getLogger("test_monitor_slot_found_fallback")
        logger.handlers = [logging.NullHandler()]
        logger.propagate = False
        profile = ApplicantProfile("jessico", ("2026-07",), "APPLICANT")

        with (
            patch.dict("os.environ", {"CONTROL_PAGE_URL": "http://100.64.0.1:8765"}),
            patch("src.monitor.send_telegram_alert", return_value=True) as send_alert,
        ):
            _send_slot_found_telegram_alert(config, profile, slot, "", None, logger)

        self.assertIn(
            "Fresh website start URL: https://example.test/start",
            send_alert.call_args.kwargs["message"],
        )
        self.assertIn(
            "Phone control page: http://100.64.0.1:8765",
            send_alert.call_args.kwargs["message"],
        )
        self.assertIsNone(send_alert.call_args.kwargs["screenshot_path"])

    def test_slot_found_alert_includes_session_url_only_as_laptop_session_context(self):
        config = AppConfig(
            website_url="https://example.test/start",
            visa_category="RWTH Studenten",
            appointment_location="RWTH - Aussenstelle Super C",
            target_months=("2026-07",),
            check_interval_seconds=10,
            stop_at_time=time(17, 0),
            headless=True,
            dry_run=True,
            browser_timeout_seconds=30,
            heartbeat_enabled=False,
            heartbeat_interval_minutes=240,
        )
        slot = SlotCandidate(
            element=None,
            date_text="2026-07-01",
            time_text="09:00",
            raw_text="Dienstag, 01.07.2026 | 09:00",
        )
        logger = logging.getLogger("test_monitor_slot_found_session_url")
        logger.handlers = [logging.NullHandler()]
        logger.propagate = False
        profile = ApplicantProfile("jessico", ("2026-07",), "APPLICANT")

        with patch("src.monitor.send_telegram_alert", return_value=True) as send_alert:
            _send_slot_found_telegram_alert(
                config,
                profile,
                slot,
                "https://example.test/session-calendar",
                None,
                logger,
            )

        self.assertIn(
            "Laptop session URL when detected: https://example.test/session-calendar",
            send_alert.call_args.kwargs["message"],
        )


if __name__ == "__main__":
    unittest.main()
