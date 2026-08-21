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
    _manual_security_direct_entry_ready,
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

    def test_env_value_falls_back_to_default_for_shared_profile_flags(self):
        profile = ApplicantProfile(
            name="applicant_b",
            target_months=("2026-09", "2026-10"),
            env_prefix="CUSTOM",
        )

        with patch.dict(os.environ, {"APPLICANT_DATA_PROCESSING_CONSENT": "true"}, clear=True):
            self.assertEqual(_env_value("APPLICANT_DATA_PROCESSING_CONSENT", profile), "true")

    def test_env_value_does_not_fall_back_to_default_for_personal_fields(self):
        profile = ApplicantProfile(
            name="applicant_b",
            target_months=("2026-09", "2026-10"),
            env_prefix="CUSTOM",
        )

        with patch.dict(os.environ, {"APPLICANT_FIRST_NAME": "Default"}, clear=True):
            self.assertEqual(_env_value("APPLICANT_FIRST_NAME", profile), "")

    def test_profile_specific_shared_flag_overrides_default_value(self):
        profile = ApplicantProfile(
            name="applicant_b",
            target_months=("2026-09", "2026-10"),
            env_prefix="CUSTOM",
        )

        with patch.dict(
            os.environ,
            {
                "APPLICANT_DATA_PROCESSING_CONSENT": "true",
                "CUSTOM_DATA_PROCESSING_CONSENT": "false",
            },
            clear=True,
        ):
            self.assertEqual(_env_value("APPLICANT_DATA_PROCESSING_CONSENT", profile), "false")


class ManualSecurityEntryTests(unittest.TestCase):
    def test_direct_entry_waits_until_field_blurs_or_complete_value_is_stable(self):
        self.assertFalse(
            _manual_security_direct_entry_ready(
                value="A",
                field_focused=True,
                last_change_at=10.0,
                now=13.0,
                min_length=4,
            )
        )
        self.assertTrue(
            _manual_security_direct_entry_ready(
                value="A",
                field_focused=False,
                last_change_at=10.0,
                now=10.1,
                min_length=4,
            )
        )
        self.assertTrue(
            _manual_security_direct_entry_ready(
                value="ABCD",
                field_focused=True,
                last_change_at=10.0,
                now=12.1,
                min_length=4,
            )
        )


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
