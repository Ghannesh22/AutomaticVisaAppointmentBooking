import sys
import tempfile
import unittest
from pathlib import Path

from src.control_server import BotProcess


class BotProcessTests(unittest.TestCase):
    def test_start_reports_immediate_exit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bot = BotProcess(
                [sys.executable, "-c", "raise SystemExit(7)"],
                Path(temp_dir),
            )

            message = bot.start()

            self.assertIn("exited immediately", message)
            self.assertIn("7", message)
            self.assertFalse(bot.is_running())
            self.assertIsNone(bot.log_handle)


if __name__ == "__main__":
    unittest.main()
