from __future__ import annotations

import os
import re
from logging import Logger

from playwright.async_api import Page


KNOWN_FIELD_VALUES = {
    "vorname": "APPLICANT_FIRST_NAME",
    "name": "APPLICANT_LAST_NAME",
    "nachname": "APPLICANT_LAST_NAME",
    "familienname": "APPLICANT_LAST_NAME",
    "e-mail": "APPLICANT_EMAIL",
    "email": "APPLICANT_EMAIL",
    "telefon": "APPLICANT_PHONE",
    "geburtsdatum": "APPLICANT_DATE_OF_BIRTH",
    "geburt": "APPLICANT_DATE_OF_BIRTH",
    "staatsangehörigkeit": "APPLICANT_NATIONALITY",
    "nationalität": "APPLICANT_NATIONALITY",
    "pass": "APPLICANT_PASSPORT_NUMBER",
    "reisepass": "APPLICANT_PASSPORT_NUMBER",
}


async def fill_personal_details(page: Page, logger: Logger) -> None:
    logger.info("Filling personal details from .env where labels can be matched")
    inputs = page.locator("input:not([type=hidden]):not([type=submit]):not([type=button]), textarea, select")
    count = await inputs.count()
    missing_required: list[str] = []

    for index in range(count):
        field = inputs.nth(index)
        if not await field.is_visible():
            continue
        label = await _label_for_field(field)
        env_name = _env_for_label(label)
        value = os.getenv(env_name, "") if env_name else ""

        if value:
            tag = await field.evaluate("el => el.tagName.toLowerCase()")
            field_type = await field.get_attribute("type") or ""
            if tag == "select":
                try:
                    await field.select_option(label=value)
                except Exception:
                    await field.select_option(value=value)
            elif field_type.lower() in {"checkbox", "radio"}:
                if value.lower() in {"1", "true", "yes", "ja"}:
                    await field.check()
            else:
                await field.fill(value)
            logger.info("Filled field '%s' from %s", label or f"#{index + 1}", env_name)
        elif await _is_required(field):
            missing_required.append(label or f"field #{index + 1}")

    if missing_required:
        joined = ", ".join(missing_required)
        raise ValueError(
            "Missing required applicant values in .env or unsupported field labels: "
            f"{joined}. Add matching APPLICANT_* values after inspecting the dry-run form."
        )


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
    normalized = re.sub(r"\s+", " ", label.lower())
    for needle, env_name in KNOWN_FIELD_VALUES.items():
        if needle in normalized:
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
