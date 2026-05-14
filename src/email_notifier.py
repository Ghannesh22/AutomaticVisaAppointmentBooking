from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path


@dataclass(frozen=True)
class EmailSettings:
    host: str
    port: int
    use_tls: bool
    sender: str
    app_password: str
    receiver: str


def load_email_settings() -> EmailSettings:
    return EmailSettings(
        host=os.getenv("SMTP_HOST", "smtp.gmail.com"),
        port=int(os.getenv("SMTP_PORT", "587")),
        use_tls=os.getenv("SMTP_USE_TLS", "true").lower() == "true",
        sender=os.getenv("EMAIL_SENDER", ""),
        app_password=os.getenv("EMAIL_APP_PASSWORD", ""),
        receiver=os.getenv("EMAIL_RECEIVER", ""),
    )


def send_booking_email(
    appointment_date: str,
    appointment_time: str,
    location: str,
    reference: str | None,
    attachment: Path | None = None,
) -> None:
    settings = load_email_settings()
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
        raise ValueError(f"Missing email settings in .env: {', '.join(missing)}")

    message = EmailMessage()
    message["Subject"] = "Visa appointment booked"
    message["From"] = settings.sender
    message["To"] = settings.receiver
    message.set_content(
        "\n".join(
            [
                "A visa appointment booking was submitted.",
                "",
                f"Date: {appointment_date}",
                f"Time: {appointment_time}",
                f"Location: {location}",
                f"Reference: {reference or 'Not visible on page'}",
                "",
                "Important: the official website may send a confirmation email with a link.",
                "Check that official email immediately and click the confirmation link if required.",
            ]
        )
    )

    if attachment and attachment.exists():
        message.add_attachment(
            attachment.read_bytes(),
            maintype="image",
            subtype="png",
            filename=attachment.name,
        )

    with smtplib.SMTP(settings.host, settings.port, timeout=30) as smtp:
        if settings.use_tls:
            smtp.starttls()
        smtp.login(settings.sender, settings.app_password)
        smtp.send_message(message)
