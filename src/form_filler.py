from __future__ import annotations

import os
import re
from dataclasses import dataclass
from logging import Logger

from playwright.async_api import Page


FIELD_MAPPINGS = [
    (("vorname",), "APPLICANT_FIRST_NAME"),
    (("nachname", "familienname"), "APPLICANT_LAST_NAME"),
    (("e-mail", "email"), "APPLICANT_EMAIL"),
    (("telefon", "mobil"), "APPLICANT_PHONE"),
    (("geburtsdatum", "geburt"), "APPLICANT_DATE_OF_BIRTH"),
    (("staatsangehoerigkeit", "staatsangehörigkeit", "nationalitaet", "nationalität"), "APPLICANT_NATIONALITY"),
    (("reisepass", "passnummer", "pass"), "APPLICANT_PASSPORT_NUMBER"),
    (("geschlecht", "gender", "anrede"), "APPLICANT_GENDER"),
]


@dataclass
class FormField:
    index: int
    label: str
    env_name: str | None
    required: bool


async def fill_personal_details(page: Page, logger: Logger) -> None:
    logger.info("Detecting Step 5 personal detail fields")
    inputs = page.locator("input:not([type=hidden]):not([type=submit]):not([type=button]), textarea, select")
    fields = await _detect_fields(inputs, logger)
    _validate_required_values(fields)

    logger.info("Filling Step 5 personal details from .env")
    for field_info in fields:
        if not field_info.env_name:
            continue
        value = os.getenv(field_info.env_name, "").strip()
        if not value:
            continue

        field = inputs.nth(field_info.index)
        tag = await field.evaluate("el => el.tagName.toLowerCase()")
        field_type = (await field.get_attribute("type") or "").lower()
        if tag == "select":
            await _select_option(field, value)
        elif field_type in {"checkbox", "radio"}:
            if value.lower() in {"1", "true", "yes", "ja"}:
                await field.check()
        else:
            value = await _value_for_field(field, field_info.env_name, value)
            await field.fill(value)
        logger.info("Filled field '%s' from %s", field_info.label, field_info.env_name)


async def _detect_fields(inputs, logger: Logger) -> list[FormField]:
    fields: list[FormField] = []
    count = await inputs.count()
    for index in range(count):
        field = inputs.nth(index)
        if not await field.is_visible():
            continue
        label = await _label_for_field(field)
        fields.append(
            FormField(
                index=index,
                label=label or f"field #{index + 1}",
                env_name=_env_for_label(label),
                required=await _is_required(field),
            )
        )

    _log_detected_fields(fields, logger)
    return fields


async def _label_for_field(field) -> str:
    return await field.evaluate(
        """el => {
            const id = el.id;
            const labels = [];
            if (id) {
                const explicit = document.querySelector(`label[for="${CSS.escape(id)}"]`);
                if (explicit) labels.push(explicit.innerText || explicit.textContent || '');
            }
            if (el.closest('label')) labels.push(el.closest('label').innerText || '');
            labels.push(el.getAttribute('aria-label') || '');
            labels.push(el.getAttribute('placeholder') || '');
            labels.push(el.name || '');
            return labels.filter(Boolean).join(' ').replace(/\\s+/g, ' ').trim();
        }"""
    )


def _env_for_label(label: str) -> str | None:
    normalized = _normalize_label(label)
    for needles, env_name in FIELD_MAPPINGS:
        if any(needle in normalized for needle in needles):
            return env_name
    return None


async def _is_required(field) -> bool:
    required = await field.get_attribute("required")
    aria_required = await field.get_attribute("aria-required")
    label_text = await field.evaluate(
        """el => {
            const id = el.id;
            const label = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
            return [
                label ? label.innerText : '',
                el.getAttribute('aria-label') || '',
                el.getAttribute('placeholder') || ''
            ].join(' ');
        }"""
    )
    return required is not None or aria_required == "true" or "*" in label_text


def _validate_required_values(fields: list[FormField]) -> None:
    missing: list[str] = []
    unmapped: list[str] = []
    for field in fields:
        if not field.required:
            continue
        if not field.env_name:
            unmapped.append(field.label)
            continue
        if not os.getenv(field.env_name, "").strip():
            missing.append(f"{field.label} -> {field.env_name}")

    errors = []
    if missing:
        errors.append("missing required .env values: " + "; ".join(missing))
    if unmapped:
        errors.append("required website fields are not mapped yet: " + "; ".join(unmapped))
    if errors:
        raise ValueError("Step 5 applicant field validation failed: " + " | ".join(errors))


def _log_detected_fields(fields: list[FormField], logger: Logger) -> None:
    if not fields:
        logger.warning("No visible Step 5 personal detail fields detected")
        return
    safe_parts = []
    for field in fields:
        safe_parts.append(
            "%s%s -> %s"
            % (
                field.label,
                " (required)" if field.required else "",
                field.env_name or "UNMAPPED",
            )
        )
    logger.info("step5_fields_detected | %s", " | ".join(safe_parts))


def _normalize_label(label: str) -> str:
    normalized = label.lower()
    replacements = {
        "ä": "ae",
        "ö": "oe",
        "ü": "ue",
        "ß": "ss",
    }
    for source, target in replacements.items():
        normalized = normalized.replace(source, target)
    return re.sub(r"\s+", " ", normalized)


async def _select_option(field, value: str) -> None:
    last_error: Exception | None = None
    for candidate in _select_value_candidates(value):
        try:
            await field.select_option(label=candidate)
            return
        except Exception as exc:
            last_error = exc
        try:
            await field.select_option(value=candidate)
            return
        except Exception as exc:
            last_error = exc
    if last_error:
        raise last_error


def _select_value_candidates(value: str) -> list[str]:
    normalized = value.strip().lower()
    if normalized in {"male", "m", "mann", "maennlich", "männlich", "herr"}:
        return [value, "male", "männlich", "maennlich", "m", "Herr"]
    if normalized in {"female", "f", "frau", "weiblich"}:
        return [value, "female", "weiblich", "f", "Frau"]
    return [value]


async def _value_for_field(field, env_name: str | None, value: str) -> str:
    if env_name != "APPLICANT_DATE_OF_BIRTH":
        return value

    field_type = (await field.get_attribute("type") or "").lower()
    placeholder = (await field.get_attribute("placeholder") or "").lower()
    pattern = (await field.get_attribute("pattern") or "").lower()
    parsed = _parse_date(value)
    if not parsed:
        return value

    day, month, year = parsed
    if field_type == "date":
        return f"{year:04d}-{month:02d}-{day:02d}"
    if "." in placeholder or "tt" in placeholder or "dd.mm" in pattern:
        return f"{day:02d}.{month:02d}.{year:04d}"
    if "/" in placeholder:
        return f"{day:02d}/{month:02d}/{year:04d}"
    return f"{day:02d}.{month:02d}.{year:04d}"


def _parse_date(value: str) -> tuple[int, int, int] | None:
    text = value.strip()
    match = re.fullmatch(r"(\d{1,2})[-./](\d{1,2})[-./](\d{4})", text)
    if match:
        day, month, year = map(int, match.groups())
        return day, month, year
    match = re.fullmatch(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})", text)
    if match:
        year, month, day = map(int, match.groups())
        return day, month, year
    return None
