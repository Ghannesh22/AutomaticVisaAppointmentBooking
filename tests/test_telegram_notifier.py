import unittest
from unittest.mock import patch

import urllib.error

from src import telegram_notifier


class TelegramNotifierTests(unittest.TestCase):
    def test_chat_ids_accepts_single_multiple_and_deduplicates(self):
        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_CHAT_ID": "111, 222",
                "TELEGRAM_CHAT_IDS": "222\n333;444",
            },
            clear=True,
        ), patch("src.telegram_notifier._read_subscriber_state", return_value={"subscribers": {}}):
            self.assertEqual(
                telegram_notifier._chat_ids(),
                ["111", "222", "333", "444"],
            )

    def test_chat_ids_include_stored_subscribers(self):
        state = {
            "subscribers": {
                "222": {"chat_id": "222"},
                "333": {"chat_id": "333"},
            }
        }
        with (
            patch.dict("os.environ", {"TELEGRAM_CHAT_ID": "111,222"}, clear=True),
            patch("src.telegram_notifier._read_subscriber_state", return_value=state),
        ):
            self.assertEqual(telegram_notifier._chat_ids(), ["111", "222", "333"])

    def test_send_telegram_alert_sends_to_each_configured_chat_id(self):
        with (
            patch.dict(
                "os.environ",
                {
                    "TELEGRAM_BOT_TOKEN": "token",
                    "TELEGRAM_CHAT_ID": "111,222",
                    "TELEGRAM_ALERT_THROTTLE_SECONDS": "300",
                },
                clear=True,
            ),
            patch("src.telegram_notifier.refresh_telegram_subscribers", return_value=0),
            patch("src.telegram_notifier._read_subscriber_state", return_value={"subscribers": {}}),
            patch("src.telegram_notifier._should_send", return_value=True),
            patch("src.telegram_notifier._send_message") as send_message,
            patch("src.telegram_notifier._record_sent") as record_sent,
        ):
            sent = telegram_notifier.send_telegram_alert(
                title="Test",
                message="Body",
                severity="info",
            )

        self.assertTrue(sent)
        self.assertEqual(
            [call.args[1] for call in send_message.call_args_list],
            ["111", "222"],
        )
        record_sent.assert_called_once()

    def test_send_telegram_alert_continues_after_one_chat_fails(self):
        def send_message(_token, chat_id, _text):
            if chat_id == "111":
                raise urllib.error.URLError("forbidden")

        with (
            patch.dict(
                "os.environ",
                {
                    "TELEGRAM_BOT_TOKEN": "token",
                    "TELEGRAM_CHAT_ID": "111,222",
                    "TELEGRAM_ALERT_THROTTLE_SECONDS": "300",
                },
                clear=True,
            ),
            patch("src.telegram_notifier.refresh_telegram_subscribers", return_value=0),
            patch("src.telegram_notifier._read_subscriber_state", return_value={"subscribers": {}}),
            patch("src.telegram_notifier._should_send", return_value=True),
            patch("src.telegram_notifier._send_message", side_effect=send_message) as send,
            patch("src.telegram_notifier._record_sent") as record_sent,
        ):
            sent = telegram_notifier.send_telegram_alert(
                title="Test",
                message="Body",
                severity="info",
            )

        self.assertTrue(sent)
        self.assertEqual([call.args[1] for call in send.call_args_list], ["111", "222"])
        record_sent.assert_called_once()

    def test_refresh_telegram_subscribers_saves_start_chat(self):
        state = {"last_update_id": 10, "subscribers": {}}
        updates = [
            {
                "update_id": 11,
                "message": {
                    "text": "/start",
                    "chat": {
                        "id": 555,
                        "type": "private",
                        "first_name": "Ada",
                        "username": "ada_user",
                    },
                },
            }
        ]
        with (
            patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "token"}, clear=True),
            patch("src.telegram_notifier._read_subscriber_state", return_value=state),
            patch("src.telegram_notifier._get_updates", return_value=updates) as get_updates,
            patch("src.telegram_notifier._try_send_message") as try_send,
            patch("src.telegram_notifier._write_subscriber_state") as write_state,
        ):
            count = telegram_notifier.refresh_telegram_subscribers(force=True)

        self.assertEqual(count, 1)
        get_updates.assert_called_once_with("token", offset=11)
        try_send.assert_called_once()
        saved_state = write_state.call_args.args[0]
        self.assertEqual(saved_state["last_update_id"], 11)
        self.assertIn("555", saved_state["subscribers"])
        self.assertEqual(saved_state["subscribers"]["555"]["username"], "ada_user")

    def test_refresh_telegram_subscribers_removes_stop_chat(self):
        state = {"last_update_id": 2, "subscribers": {"555": {"chat_id": "555"}}}
        updates = [
            {
                "update_id": 3,
                "message": {"text": "/stop", "chat": {"id": 555, "type": "private"}},
            }
        ]
        with (
            patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "token"}, clear=True),
            patch("src.telegram_notifier._read_subscriber_state", return_value=state),
            patch("src.telegram_notifier._get_updates", return_value=updates),
            patch("src.telegram_notifier._try_send_message"),
            patch("src.telegram_notifier._write_subscriber_state") as write_state,
        ):
            count = telegram_notifier.refresh_telegram_subscribers(force=True)

        self.assertEqual(count, 0)
        saved_state = write_state.call_args.args[0]
        self.assertNotIn("555", saved_state["subscribers"])


if __name__ == "__main__":
    unittest.main()
