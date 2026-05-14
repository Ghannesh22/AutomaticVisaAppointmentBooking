from __future__ import annotations

import smtplib
import sys
from email.message import EmailMessage

from dotenv import load_dotenv

from src.config_loader import PROJECT_ROOT
from src.email_notifier import load_email_settings, smtp_enabled
from src.logger import setup_logger


def main() -> int:
    logger = setup_logger()
    load_dotenv(PROJECT_ROOT / ".env")
    settings = load_email_settings()
    if not smtp_enabled():
        logger.info("SMTP is disabled because SMTP settings are empty")
        return 0

    missing = [
        key
        for key, value in {
            "EMAIL_SENDER": settings.sender,
            "EMAIL_APP_PASSWORD": settings.app_password,
            "EMAIL_RECEIVER": settings.receiver,
        }.items()
        if not value
    ]
    if missing:
        logger.error("SMTP test failed: missing .env values: %s", ", ".join(missing))
        return 1

    message = EmailMessage()
    message["Subject"] = "Visa appointment monitor SMTP test"
    message["From"] = settings.sender
    message["To"] = settings.receiver
    message.set_content("SMTP test email sent successfully from the visa appointment monitor project.")

    try:
        with smtplib.SMTP(settings.host, settings.port, timeout=30) as smtp:
            if settings.use_tls:
                smtp.starttls()
            smtp.login(settings.sender, settings.app_password)
            smtp.send_message(message)
    except Exception as exc:
        logger.error("SMTP test failed: %s", exc)
        return 1

    logger.info("SMTP test email sent successfully to %s", settings.receiver)
    return 0


if __name__ == "__main__":
    sys.exit(main())
