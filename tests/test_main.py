import unittest
from unittest.mock import Mock, patch

from src.main import _send_unhandled_error_telegram_alert


class MainTelegramAlertTests(unittest.TestCase):
    def test_unhandled_error_alert_is_forced_fatal(self):
        logger = Mock()
        error = RuntimeError("browser failed to launch")

        with patch("src.main.send_telegram_alert", return_value=True) as send_alert:
            _send_unhandled_error_telegram_alert(error, logger)

        send_alert.assert_called_once()
        kwargs = send_alert.call_args.kwargs
        self.assertEqual(kwargs["title"], "Bot stopped because of an unexpected error")
        self.assertEqual(kwargs["message"], "browser failed to launch")
        self.assertEqual(kwargs["severity"], "fatal")
        self.assertTrue(kwargs["force"])
        logger.info.assert_called_once_with("Telegram unhandled error alert sent")


if __name__ == "__main__":
    unittest.main()
