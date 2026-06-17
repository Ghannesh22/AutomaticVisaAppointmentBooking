import unittest
from pathlib import Path
import os
from unittest.mock import Mock, patch

from src.config_loader import ApplicantProfile
from src.form_filler import (
    FormField,
    _assign_split_date_fields,
    _env_value,
    _env_for_label,
    _profile_env_name,
    _send_manual_security_telegram_alert,
)


class FieldMappingTests(unittest.TestCase):
    def test_maps_security_string_label_to_manual_answer(self):
        self.assertEqual(_env_for_label("String:"), "APPLICANT_SECURITY_ANSWER")

    def test_assigns_split_birth_date_fields_in_visible_order(self):
        fields = [
            FormField(0, "Birth date * day Month Year", "APPLICANT_DATE_OF_BIRTH", True, "text"),
            FormField(1, "Birth date * day Month Year", "APPLICANT_DATE_OF_BIRTH", True, "text"),
            FormField(2, "Birth date * day Month Year", "APPLICANT_DATE_OF_BIRTH", True, "text"),
        ]

        _assign_split_date_fields(fields)

        self.assertEqual(
            [field.env_name for field in fields],
            [
                "APPLICANT_DATE_OF_BIRTH_DAY",
                "APPLICANT_DATE_OF_BIRTH_MONTH",
                "APPLICANT_DATE_OF_BIRTH_YEAR",
            ],
        )


class ApplicantProfileEnvTests(unittest.TestCase):
    def test_profile_env_name_uses_configured_prefix(self):
        profile = ApplicantProfile(
            name="applicant_b",
            target_months=("2026-09", "2026-10"),
            env_prefix="CUSTOM",
        )

        self.assertEqual(
            _profile_env_name("APPLICANT_FIRST_NAME", profile),
            "CUSTOM_FIRST_NAME",
        )

    def test_env_value_reads_profile_prefixed_date_components(self):
        profile = ApplicantProfile(
            name="applicant_b",
            target_months=("2026-09", "2026-10"),
            env_prefix="CUSTOM",
        )

        with patch.dict(os.environ, {"CUSTOM_DATE_OF_BIRTH": "05-09-2001"}, clear=True):
            self.assertEqual(_env_value("APPLICANT_DATE_OF_BIRTH_DAY", profile), "05")
            self.assertEqual(_env_value("APPLICANT_DATE_OF_BIRTH_MONTH", profile), "09")
            self.assertEqual(_env_value("APPLICANT_DATE_OF_BIRTH_YEAR", profile), "2001")


class ManualSecurityTelegramAlertTests(unittest.TestCase):
    def test_manual_security_alert_is_forced_and_includes_screenshot(self):
        logger = Mock()
        screenshot = Path("logs/security_challenge.png")

        with patch("src.form_filler.send_telegram_alert", return_value=True) as send_alert:
            _send_manual_security_telegram_alert("String:", screenshot, logger)

        send_alert.assert_called_once()
        kwargs = send_alert.call_args.kwargs
        self.assertEqual(kwargs["title"], "Manual security answer required")
        self.assertEqual(kwargs["severity"], "manual_action")
        self.assertEqual(kwargs["screenshot_path"], screenshot)
        self.assertTrue(kwargs["force"])
        self.assertIn("Field: String:", kwargs["message"])
        logger.info.assert_called_once_with("Telegram manual security alert sent")


if __name__ == "__main__":
    unittest.main()
