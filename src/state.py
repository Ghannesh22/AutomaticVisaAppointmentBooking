from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from src.config_loader import PROJECT_ROOT


SUCCESS_FLAG_PATH = PROJECT_ROOT / "confirmations" / "booking_success.flag"


def has_success_flag(profile_name: str | None = None) -> bool:
    return success_flag_path(profile_name).exists()


def success_flag_path(profile_name: str | None = None) -> Path:
    if profile_name is None:
        return SUCCESS_FLAG_PATH
    return PROJECT_ROOT / "confirmations" / f"booking_success_{_safe_profile_name(profile_name)}.flag"


def write_success_flag(
    appointment_date: str,
    appointment_time: str,
    location: str,
    reference: str | None,
    profile_name: str | None = None,
) -> Path:
    path = success_flag_path(profile_name)
    path.parent.mkdir(exist_ok=True)
    path.write_text(
        "\n".join(
            [
                f"created_at={datetime.now().isoformat(timespec='seconds')}",
                f"profile_name={profile_name or 'default'}",
                f"appointment_date={appointment_date}",
                f"appointment_time={appointment_time}",
                f"location={location}",
                f"reference={reference or ''}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _safe_profile_name(profile_name: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", profile_name.strip())
    return safe.strip("._-") or "default"
