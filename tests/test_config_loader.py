import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.config_loader import load_config, load_project_env


class EnvLoadingTests(unittest.TestCase):
    def test_load_project_env_accepts_utf8_bom(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text("\ufeffAPPLICANT_FIRST_NAME=Example\n", encoding="utf-8")

            with patch.dict(os.environ, {}, clear=True):
                load_project_env(env_path)

                self.assertEqual(os.environ["APPLICANT_FIRST_NAME"], "Example")
                self.assertNotIn("\ufeffAPPLICANT_FIRST_NAME", os.environ)

    def test_load_project_env_overrides_inherited_empty_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text("APPLICANT_2_FIRST_NAME=Second\n", encoding="utf-8")

            with patch.dict(os.environ, {"APPLICANT_2_FIRST_NAME": ""}, clear=True):
                load_project_env(env_path)

                self.assertEqual(os.environ["APPLICANT_2_FIRST_NAME"], "Second")


class ApplicantProfileConfigTests(unittest.TestCase):
    def test_load_config_flattens_profile_target_months(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            config_path.write_text(
                """
website_url: "https://example.test"
visa_category: "RWTH Studenten"
appointment_location: "RWTH"
applicant_profiles:
  - name: "jessico"
    env_prefix: "APPLICANT"
    target_months: ["2026-07", "2026-08"]
  - name: "sammed"
    env_prefix: "APPLICANT_2"
    target_months: ["2026-09", "2026-10"]
check_interval_seconds: 10
stop_at_time: "17:00"
browser_timeout_seconds: 30
heartbeat_interval_minutes: 240
""",
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertEqual(config.target_months, ("2026-07", "2026-08", "2026-09", "2026-10"))
            self.assertEqual(config.profile_for_month("2026-07").name, "jessico")
            self.assertEqual(config.profile_for_month("2026-10").name, "sammed")
            self.assertEqual(
                config.target_months_for_open_profiles({"jessico"}),
                ("2026-09", "2026-10"),
            )


if __name__ == "__main__":
    unittest.main()
