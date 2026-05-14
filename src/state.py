from __future__ import annotations

from datetime import datetime
from pathlib import Path

from src.config_loader import PROJECT_ROOT


SUCCESS_FLAG_PATH = PROJECT_ROOT / "confirmations" / "booking_success.flag"


def has_success_flag() -> bool:
    return SUCCESS_FLAG_PATH.exists()


def success_flag_path() -> Path:
    return SUCCESS_FLAG_PATH


def write_success_flag(
    appointment_date: str,
    appointment_time: str,
    location: str,
    reference: str | None,
) -> Path:
    SUCCESS_FLAG_PATH.parent.mkdir(exist_ok=True)
    SUCCESS_FLAG_PATH.write_text(
        "\n".join(
            [
                f"created_at={datetime.now().isoformat(timespec='seconds')}",
                f"appointment_date={appointment_date}",
                f"appointment_time={appointment_time}",
                f"location={location}",
                f"reference={reference or ''}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return SUCCESS_FLAG_PATH
