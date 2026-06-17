from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class ApplicantProfile:
    name: str
    target_months: tuple[str, ...]
    env_prefix: str = "APPLICANT"


@dataclass(frozen=True)
class AppConfig:
    website_url: str
    visa_category: str
    appointment_location: str
    target_months: tuple[str, ...]
    check_interval_seconds: int
    stop_at_time: time
    headless: bool
    dry_run: bool
    browser_timeout_seconds: int
    heartbeat_enabled: bool
    heartbeat_interval_minutes: int
    applicant_profiles: tuple[ApplicantProfile, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.applicant_profiles:
            object.__setattr__(
                self,
                "applicant_profiles",
                (
                    ApplicantProfile(
                        name="default",
                        target_months=self.target_months,
                        env_prefix="APPLICANT",
                    ),
                ),
            )
        if not self.target_months:
            months: list[str] = []
            seen: set[str] = set()
            for profile in self.applicant_profiles:
                for month in profile.target_months:
                    if month not in seen:
                        months.append(month)
                        seen.add(month)
            object.__setattr__(self, "target_months", tuple(months))

    @property
    def target_month(self) -> str:
        return self.target_months[0]

    @property
    def target_year(self) -> int:
        return int(self.target_month.split("-")[0])

    @property
    def target_month_number(self) -> int:
        return int(self.target_month.split("-")[1])

    def profile_for_month(self, month: str) -> ApplicantProfile:
        for profile in self.applicant_profiles:
            if month in profile.target_months:
                return profile
        raise ValueError(f"No applicant profile configured for target month {month}")

    def target_months_for_open_profiles(self, completed_profiles: set[str]) -> tuple[str, ...]:
        months: list[str] = []
        seen: set[str] = set()
        for profile in self.applicant_profiles:
            if profile.name in completed_profiles:
                continue
            for month in profile.target_months:
                if month not in seen:
                    months.append(month)
                    seen.add(month)
        return tuple(months)


def load_config(path: Path | None = None) -> AppConfig:
    load_project_env()
    config_path = path or PROJECT_ROOT / "config.yaml"
    raw = _read_yaml(config_path)

    config = AppConfig(
        website_url=str(raw["website_url"]).strip(),
        visa_category=str(raw["visa_category"]).strip(),
        appointment_location=str(raw["appointment_location"]).strip(),
        target_months=_target_months(raw),
        check_interval_seconds=int(raw.get("check_interval_seconds", 120)),
        stop_at_time=_parse_stop_at_time(raw.get("stop_at_time", "17:00")),
        headless=_as_bool(raw.get("headless", False)),
        dry_run=_as_bool(raw.get("dry_run", True)),
        browser_timeout_seconds=int(raw.get("browser_timeout_seconds", 30)),
        heartbeat_enabled=_as_bool(raw.get("heartbeat_enabled", False)),
        heartbeat_interval_minutes=int(raw.get("heartbeat_interval_minutes", 240)),
        applicant_profiles=_applicant_profiles(raw),
    )
    _validate_config(config)
    return config


def load_project_env(path: Path | None = None) -> None:
    load_dotenv(path or PROJECT_ROOT / ".env", encoding="utf-8-sig", override=True)


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
    if not config.target_months:
        raise ValueError("At least one target month is required")
    for month in config.target_months:
        datetime.strptime(month, "%Y-%m")
    profile_names: set[str] = set()
    profile_months: dict[str, str] = {}
    for profile in config.applicant_profiles:
        if not profile.name:
            raise ValueError("Each applicant profile must have a name")
        if profile.name in profile_names:
            raise ValueError(f"Duplicate applicant profile name: {profile.name}")
        profile_names.add(profile.name)
        if not profile.target_months:
            raise ValueError(f"Applicant profile {profile.name} must define target_months")
        for month in profile.target_months:
            datetime.strptime(month, "%Y-%m")
            if month in profile_months:
                raise ValueError(
                    f"Target month {month} is assigned to both {profile_months[month]} and {profile.name}"
                )
            profile_months[month] = profile.name
        if not profile.env_prefix:
            raise ValueError(f"Applicant profile {profile.name} must define env_prefix")
    if config.check_interval_seconds < 10:
        raise ValueError("check_interval_seconds must be at least 10")
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


def _parse_stop_at_time(value: Any) -> time:
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    text = str(value).strip()
    try:
        return datetime.strptime(text, "%H:%M").time()
    except ValueError as exc:
        raise ValueError("stop_at_time must use 24-hour HH:MM format") from exc


def _target_months(raw: dict[str, Any]) -> tuple[str, ...]:
    profiles = _applicant_profiles(raw)
    if profiles:
        return _unique_months(month for profile in profiles for month in profile.target_months)

    value = raw.get("target_months", raw.get("target_month"))
    if value is None:
        raise ValueError("config.yaml must define target_months or target_month")
    if isinstance(value, str):
        months = [value]
    elif isinstance(value, list):
        months = value
    else:
        raise ValueError("target_months must be a list of YYYY-MM values")

    return _unique_months(str(item).strip() for item in months)


def _applicant_profiles(raw: dict[str, Any]) -> tuple[ApplicantProfile, ...]:
    value = raw.get("applicant_profiles")
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("applicant_profiles must be a list")

    profiles: list[ApplicantProfile] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise ValueError("Each applicant_profiles item must be a mapping")
        name = str(item.get("name", f"applicant_{index}")).strip()
        env_prefix = str(item.get("env_prefix", "APPLICANT")).strip()
        months_value = item.get("target_months", item.get("target_month"))
        if months_value is None:
            raise ValueError(f"Applicant profile {name} must define target_months or target_month")
        if isinstance(months_value, str):
            months = [months_value]
        elif isinstance(months_value, list):
            months = months_value
        else:
            raise ValueError(f"Applicant profile {name} target_months must be a list of YYYY-MM values")
        profiles.append(
            ApplicantProfile(
                name=name,
                target_months=_unique_months(str(month).strip() for month in months),
                env_prefix=env_prefix,
            )
        )
    return tuple(profiles)


def _unique_months(months: Any) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for item in months:
        month = str(item).strip()
        if not month or month in seen:
            continue
        normalized.append(month)
        seen.add(month)
    return tuple(normalized)
