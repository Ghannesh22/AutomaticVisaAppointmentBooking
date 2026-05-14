from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class AppConfig:
    website_url: str
    visa_category: str
    appointment_location: str
    target_month: str
    check_interval_seconds: int
    max_runtime_minutes: int
    headless: bool
    dry_run: bool
    browser_timeout_seconds: int
    heartbeat_enabled: bool
    heartbeat_interval_minutes: int

    @property
    def target_year(self) -> int:
        return int(self.target_month.split("-")[0])

    @property
    def target_month_number(self) -> int:
        return int(self.target_month.split("-")[1])


def load_config(path: Path | None = None) -> AppConfig:
    load_dotenv(PROJECT_ROOT / ".env")
    config_path = path or PROJECT_ROOT / "config.yaml"
    raw = _read_yaml(config_path)

    config = AppConfig(
        website_url=str(raw["website_url"]).strip(),
        visa_category=str(raw["visa_category"]).strip(),
        appointment_location=str(raw["appointment_location"]).strip(),
        target_month=str(raw["target_month"]).strip(),
        check_interval_seconds=int(raw.get("check_interval_seconds", 300)),
        max_runtime_minutes=int(raw.get("max_runtime_minutes", 720)),
        headless=_as_bool(raw.get("headless", False)),
        dry_run=_as_bool(raw.get("dry_run", True)),
        browser_timeout_seconds=int(raw.get("browser_timeout_seconds", 30)),
        heartbeat_enabled=_as_bool(raw.get("heartbeat_enabled", False)),
        heartbeat_interval_minutes=int(raw.get("heartbeat_interval_minutes", 240)),
    )
    _validate_config(config)
    return config


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing config file: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError("config.yaml must contain a mapping")
    return data


def _validate_config(config: AppConfig) -> None:
    if not config.website_url.startswith(("http://", "https://")):
        raise ValueError("website_url must be an http(s) URL")
    datetime.strptime(config.target_month, "%Y-%m")
    if config.check_interval_seconds < 60:
        raise ValueError("check_interval_seconds must be at least 60")
    if config.max_runtime_minutes < 1:
        raise ValueError("max_runtime_minutes must be at least 1")
    if config.browser_timeout_seconds < 5:
        raise ValueError("browser_timeout_seconds must be at least 5")
    if config.heartbeat_interval_minutes < 15:
        raise ValueError("heartbeat_interval_minutes must be at least 15")


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)
