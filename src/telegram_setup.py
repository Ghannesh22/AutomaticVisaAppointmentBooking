from __future__ import annotations

import os

from dotenv import load_dotenv

from src.config_loader import PROJECT_ROOT
from src.telegram_notifier import refresh_telegram_subscribers, telegram_subscriber_chat_ids


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("Set TELEGRAM_BOT_TOKEN in .env first.")
        print("Create a bot with @BotFather in Telegram, then paste the token into .env.")
        return 1

    count = refresh_telegram_subscribers(force=True)
    chat_ids = telegram_subscriber_chat_ids()
    if not chat_ids:
        print("No Telegram subscribers found for this bot yet.")
        print("Open Telegram, send /start to your bot, then run this command again.")
        return 1

    print(f"Saved /start subscribers: {count}")
    print()
    print("Alerts will be sent to:")
    for chat_id in chat_ids:
        print(f"- {chat_id}")
    print()
    print("Future recipients can join by sending /start to the bot.")
    print("TELEGRAM_CHAT_ID is now optional unless you want to force static recipients.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
