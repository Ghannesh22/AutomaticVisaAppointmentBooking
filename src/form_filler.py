from __future__ import annotations

import os
import re
import sys
import time
from dataclasses import dataclass
from logging import Logger

from playwright.async_api import Page

from src.config_loader import ApplicantProfile, PROJECT_ROOT
from src.security_state import (
    clear_security_request,
    consume_security_answer,
    create_security_request,
)
from src.telegram_notifier import send_telegram_alert


FIELD_MAPPINGS = [
    (
        "APPLICANT_SAVE_PERSONAL_DATA_LOCALLY",
        (
            "personenbezogene daten lokal",
            "persoenliche daten lokal",
            "persoenliche daten lokal speichern",
            "daten lokal speichern",
            "lokal speichern",
            "fuer kuenftige anfragen speichern",
            "fuer zukuenftige anfragen speichern",
            "zukuenftige anfragen",
            "future requests",
            "personal data locally",
        ),
    ),
    (
        "APPLICANT_DATA_PROCESSING_CONSENT",
        (
            "einwilligung",
            "einverstaendnis",
            "ich stimme",
            "ich bin einverstanden",
            "datenverarbeitung",
            "verarbeitung meiner daten",
            "verarbeitung personenbezogener daten",
            "online terminbuchung",
            "terminbuchung",
            "datenschutz",
            "datenschutzerklaerung",
            "datenschutzgrundverordnung",
            "dsgvo",
            "gdpr",
            "art 6",
            "agb",
            "processing of my data",
            "online appointment booking",
        ),
    ),
    (
        "APPLICANT_EMAIL",
        (
            "e mail wiederholen",
            "email wiederholen",
            "e mail wiederholung",
            "email wiederholung",
            "e mail bestaetigen",
            "email bestaetigen",
            "e mail erneut",
            "email erneut",
            "e mail repeat",
            "email repeat",
            "e mail adresse wiederholen",
            "mailadresse wiederholen",
        ),
    ),
    (
        "APPLICANT_FIRST_NAME",
        (
            "vorname",
            "vornamen",
            "rufname",
            "given name",
            "first name",
        ),
    ),
    (
        "APPLICANT_LAST_NAME",
        (
            "nachname",
            "familienname",
            "zuname",
            "surname",
            "last name",
            "family name",
        ),
    ),
    (
        "APPLICANT_EMAIL",
        (
            "e mail",
            "email",
            "mailadresse",
            "e mail adresse",
            "email adresse",
            "elektronische post",
        ),
    ),
    (
        "APPLICANT_PHONE",
        (
            "telefon",
            "telefonnummer",
            "rufnummer",
            "mobil",
            "mobilnummer",
            "mobiltelefon",
            "handy",
            "handynummer",
            "phone",
            "telephone",
            "mobile",
        ),
    ),
    (
        "APPLICANT_DATE_OF_BIRTH",
        (
            "geburtsdatum",
            "datum der geburt",
            "geburt datum",
            "geburtstag",
            "geboren am",
            "date of birth",
            "birth date",
            "birthday",
        ),
    ),
    (
        "APPLICANT_REMARKS",
        (
            "bemerkung",
            "bemerkungen",
            "anmerkung",
            "anmerkungen",
            "hinweis",
            "hinweise",
            "mitteilung",
            "nachricht",
            "kommentar",
            "notiz",
            "remarks",
            "comment",
            "message",
            "notes",
        ),
    ),
    (
        "APPLICANT_SECURITY_ANSWER",
        (
            "sicherheitsfrage",
            "sicherheitsabfrage",
            "sicherheitsantwort",
            "sicherheits antwort",
            "antwort auf die sicherheitsfrage",
            "sicherheitscode",
            "pruefcode",
            "prueffrage",
            "kontrollfrage",
            "captcha",
            "zeichenfolge",
            "string",
            "security question",
            "security answer",
            "security code",
        ),
    ),
    (
        "APPLICANT_NATIONALITY",
        (
            "staatsangehoerigkeit",
            "nationalitaet",
            "nation",
            "nationality",
            "citizenship",
        ),
    ),
    (
        "APPLICANT_PASSPORT_NUMBER",
        (
            "reisepass",
            "passnummer",
            "pass nummer",
            "ausweisnummer",
            "dokumentnummer",
            "passport",
            "passport number",
        ),
    ),
    (
        "APPLICANT_GENDER",
        (
            "geschlecht",
            "anrede",
            "titel",
            "gender",
            "salutation",
        ),
    ),
]

DATE_OF_BIRTH_HINTS = (
    "geburtsdatum",
    "datum der geburt",
    "geburt datum",
    "geburtstag",
    "geboren am",
    "date of birth",
    "birth date",
    "birthday",
)

DATE_DAY_HINTS = ("tag", "tt", "dd", "day")
DATE_MONTH_HINTS = ("monat", "mm", "month")
DATE_YEAR_HINTS = ("jahr", "jjjj", "yyyy", "year")


@dataclass
class FormField:
    index: int
    label: str
    env_name: str | None
    required: bool
    field_type: str


async def fill_personal_details(
    page: Page,
    logger: Logger,
    applicant_profile: ApplicantProfile | None = None,
) -> None:
    logger.info("Detecting Step 5 personal detail fields")
    inputs = page.locator("input:not([type=hidden]):not([type=submit]):not([type=button]), textarea, select")
    fields = await _detect_fields(inputs, logger)
    _validate_required_values(fields, applicant_profile)

    profile_name = applicant_profile.name if applicant_profile else "default"
    logger.info("Filling Step 5 personal details from .env for applicant profile '%s'", profile_name)
    for field_info in fields:
        if not field_info.env_name:
            continue
        value = _env_value(field_info.env_name, applicant_profile)
        field = inputs.nth(field_info.index)
        if not value and field_info.env_name == "APPLICANT_SECURITY_ANSWER":
            await _wait_for_manual_security_answer(field, page, logger, field_info.label)
            continue
        if not value:
            continue

        tag = await field.evaluate("el => el.tagName.toLowerCase()")
        field_type = (await field.get_attribute("type") or "").lower()
        if tag == "select":
            await _select_option(field, value)
        elif field_type in {"checkbox", "radio"}:
            if value.lower() in {"1", "true", "yes", "ja"}:
                await field.check()
        else:
            value = await _value_for_field(field, field_info.env_name, value, applicant_profile)
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
        own_hint = await _own_hint_for_field(field)
        field_type = (await field.get_attribute("type") or "").lower()
        fields.append(
            FormField(
                index=index,
                label=label or f"field #{index + 1}",
                env_name=_env_for_label(label, own_hint),
                required=await _is_required(field),
                field_type=field_type,
            )
        )

    _assign_split_date_fields(fields)
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
            labels.push(el.id || '');
            labels.push(el.name || '');

            const context = el.closest('tr, .form-group, .form-row, .row, li, fieldset, p');
            if (context) {
                const controls = context.querySelectorAll(
                    'input:not([type=hidden]):not([type=submit]):not([type=button]), textarea, select'
                );
                const contextText = context.innerText || context.textContent || '';
                if (controls.length <= 4 && contextText.length <= 500) labels.push(contextText);
            }

            return labels.filter(Boolean).join(' ').replace(/\\s+/g, ' ').trim();
        }"""
    )


async def _own_hint_for_field(field) -> str:
    return await field.evaluate(
        """el => [
            el.getAttribute('aria-label') || '',
            el.getAttribute('placeholder') || '',
            el.id || '',
            el.name || ''
        ].filter(Boolean).join(' ').replace(/\\s+/g, ' ').trim()"""
    )


def _env_for_label(label: str, own_hint: str = "") -> str | None:
    normalized = _normalize_label(label)
    own = _normalize_label(own_hint)

    date_component = _date_component_env(own, normalized)
    if date_component:
        return date_component

    for text in (own, normalized):
        env_name = _mapped_env_for_text(text)
        if env_name:
            return env_name
    return None


def _assign_split_date_fields(fields: list[FormField]) -> None:
    date_fields = [
        field
        for field in fields
        if field.env_name == "APPLICANT_DATE_OF_BIRTH"
        and _has_any_phrase(_normalize_label(field.label), DATE_OF_BIRTH_HINTS)
    ]
    if len(date_fields) != 3:
        return

    for field, env_name in zip(
        date_fields,
        (
            "APPLICANT_DATE_OF_BIRTH_DAY",
            "APPLICANT_DATE_OF_BIRTH_MONTH",
            "APPLICANT_DATE_OF_BIRTH_YEAR",
        ),
    ):
        field.env_name = env_name


def _date_component_env(own: str, label: str) -> str | None:
    if not (_has_any_phrase(own, DATE_OF_BIRTH_HINTS) or _has_any_phrase(label, DATE_OF_BIRTH_HINTS)):
        return None

    for text in (own, label):
        components = [
            _has_component_hint(text, DATE_DAY_HINTS),
            _has_component_hint(text, DATE_MONTH_HINTS),
            _has_component_hint(text, DATE_YEAR_HINTS),
        ]
        if sum(components) != 1:
            continue
        if components[0]:
            return "APPLICANT_DATE_OF_BIRTH_DAY"
        if components[1]:
            return "APPLICANT_DATE_OF_BIRTH_MONTH"
        return "APPLICANT_DATE_OF_BIRTH_YEAR"

    return "APPLICANT_DATE_OF_BIRTH"


def _mapped_env_for_text(text: str) -> str | None:
    if not text:
        return None
    for env_name, phrases in FIELD_MAPPINGS:
        if _has_any_phrase(text, phrases):
            return env_name
    return None


def _has_any_phrase(text: str, phrases: tuple[str, ...]) -> bool:
    return any(_normalize_label(phrase) in text for phrase in phrases)


def _has_component_hint(text: str, phrases: tuple[str, ...]) -> bool:
    tokens = set(text.split())
    for phrase in phrases:
        normalized = _normalize_label(phrase)
        if len(normalized) <= 4:
            if normalized in tokens:
                return True
        elif normalized in text:
            return True
    return False


async def _is_required(field) -> bool:
    required = await field.get_attribute("required")
    aria_required = await field.get_attribute("aria-required")
    label_text = await field.evaluate(
        """el => {
            const id = el.id;
            const label = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
            const parts = [
                label ? label.innerText : '',
                el.getAttribute('aria-label') || '',
                el.getAttribute('placeholder') || '',
                el.closest('label') ? el.closest('label').innerText : ''
            ];

            const context = el.closest('tr, .form-group, .form-row, .row, li, fieldset, p');
            if (context) {
                const controls = context.querySelectorAll(
                    'input:not([type=hidden]):not([type=submit]):not([type=button]), textarea, select'
                );
                const contextText = context.innerText || context.textContent || '';
                if (controls.length <= 4 && contextText.length <= 500) parts.push(contextText);
            }

            return parts.join(' ');
        }"""
    )
    return required is not None or aria_required == "true" or "*" in label_text


def _validate_required_values(
    fields: list[FormField],
    applicant_profile: ApplicantProfile | None = None,
) -> None:
    missing: list[str] = []
    unmapped: list[str] = []
    for field in fields:
        if not field.required:
            continue
        if not field.env_name:
            unmapped.append(field.label)
            continue
        value = _env_value(field.env_name, applicant_profile)
        if field.env_name == "APPLICANT_SECURITY_ANSWER" and not value:
            continue
        if not value:
            missing.append(f"{field.label} -> {_profile_env_name(field.env_name, applicant_profile)}")
            continue
        if field.field_type in {"checkbox", "radio"} and value.lower() not in {"1", "true", "yes", "ja"}:
            missing.append(
                f"{field.label} -> {_profile_env_name(field.env_name, applicant_profile)} must be true/yes/ja"
            )

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
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", label).lower()
    replacements = {
        "\u00e4": "ae",
        "\u00f6": "oe",
        "\u00fc": "ue",
        "\u00df": "ss",
        "\u00c3\u00a4": "ae",
        "\u00c3\u00b6": "oe",
        "\u00c3\u00bc": "ue",
        "\u00c3\u009f": "ss",
        "\u00c3\u0178": "ss",
    }
    for source, target in replacements.items():
        normalized = normalized.replace(source, target)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


async def _wait_for_manual_security_answer(field, page: Page, logger: Logger, label: str) -> None:
    logger.warning(
        "Manual security challenge detected. Type the visible letters/numbers into the focused field; "
        "the script will wait before continuing. You can also submit it from the phone control page."
    )
    await _notify_manual_security_required(page, logger)
    screenshot = await _save_security_challenge_screenshot(field, logger)
    challenge_id = create_security_request(label=label, image_path=screenshot)
    _send_manual_security_telegram_alert(label, screenshot, logger)
    logger.warning("phone_security_answer_required | challenge_id=%s", challenge_id)
    await field.focus()
    deadline = time.monotonic() + 600
    next_alert_at = time.monotonic() + 10
    try:
        while time.monotonic() < deadline:
            submitted_answer = consume_security_answer(challenge_id)
            if submitted_answer:
                await field.fill(submitted_answer)
                logger.info("Manual security answer received from phone; continuing booking flow")
                return

            value = await field.evaluate("el => ('value' in el ? el.value : el.textContent || '').trim()")
            if value:
                logger.info("Manual security answer entered; continuing booking flow")
                return
            if time.monotonic() >= next_alert_at:
                _play_manual_security_alert()
                next_alert_at = time.monotonic() + 10
            await page.wait_for_timeout(1000)
        raise ValueError("Timed out waiting for manual security challenge entry")
    finally:
        clear_security_request(challenge_id)


async def _save_security_challenge_screenshot(field, logger: Logger):
    path = PROJECT_ROOT / "logs" / "security_challenge.png"
    try:
        context_handle = await field.evaluate_handle(
            """el => {
                const controlsSelector = 'input:not([type=hidden]):not([type=submit]):not([type=button]), textarea, select';
                let best = el;
                for (let node = el; node && node !== document.body; node = node.parentElement) {
                    const rect = node.getBoundingClientRect();
                    const controls = node.querySelectorAll(controlsSelector).length;
                    const text = (node.innerText || node.textContent || '').replace(/\\s+/g, ' ').trim();
                    if (
                        rect.width >= 80
                        && rect.height >= 30
                        && rect.width <= 1200
                        && rect.height <= 700
                        && controls <= 4
                        && text.length <= 1000
                    ) {
                        best = node;
                    }
                }
                return best;
            }"""
        )
        element = context_handle.as_element()
        if element:
            await element.screenshot(path=str(path))
        else:
            await field.screenshot(path=str(path))
        return path
    except Exception as exc:
        logger.warning("Could not save manual security challenge screenshot for phone: %s", exc)
        return None


async def _notify_manual_security_required(page: Page, logger: Logger) -> None:
    await _restore_browser_window_if_minimized(page, logger)

    try:
        await page.bring_to_front()
    except Exception as exc:
        logger.warning("Could not bring browser to front for manual security challenge: %s", exc)

    _play_manual_security_alert()


def _send_manual_security_telegram_alert(label: str, screenshot_path, logger: Logger) -> None:
    message = "\n".join(
        [
            "The laptop bot found an appointment and is waiting for a manual security/captcha answer.",
            "",
            f"Field: {label}",
            "",
            "Open the phone control page to enter the answer, or enter it directly in the laptop browser.",
        ]
    )
    if send_telegram_alert(
        title="Manual security answer required",
        message=message,
        severity="manual_action",
        screenshot_path=screenshot_path,
        force=True,
    ):
        logger.info("Telegram manual security alert sent")


async def _restore_browser_window_if_minimized(page: Page, logger: Logger) -> None:
    try:
        session = await page.context.new_cdp_session(page)
        window = await session.send("Browser.getWindowForTarget")
        if window.get("bounds", {}).get("windowState") == "minimized":
            await session.send(
                "Browser.setWindowBounds",
                {"windowId": window["windowId"], "bounds": {"windowState": "normal"}},
            )
    except Exception as exc:
        logger.debug("Could not restore browser window before manual security challenge: %s", exc)


def _play_manual_security_alert() -> None:
    if sys.platform == "win32":
        try:
            import winsound

            for _ in range(3):
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
                time.sleep(0.2)
            return
        except RuntimeError:
            pass

    print("\a\a\a", end="", flush=True)


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
    normalized = _normalize_label(value)
    if normalized in {"male", "m", "mann", "maennlich", "herr"}:
        return [value, "male", "m\u00e4nnlich", "maennlich", "m", "Herr"]
    if normalized in {"female", "f", "frau", "weiblich"}:
        return [value, "female", "weiblich", "f", "Frau"]
    return [value]


async def _value_for_field(
    field,
    env_name: str | None,
    value: str,
    applicant_profile: ApplicantProfile | None = None,
) -> str:
    if env_name in {
        "APPLICANT_DATE_OF_BIRTH_DAY",
        "APPLICANT_DATE_OF_BIRTH_MONTH",
        "APPLICANT_DATE_OF_BIRTH_YEAR",
    }:
        return _env_value(env_name, applicant_profile) or value

    if env_name != "APPLICANT_DATE_OF_BIRTH":
        return value

    field_type = (await field.get_attribute("type") or "").lower()
    placeholder = _normalize_label(await field.get_attribute("placeholder") or "")
    pattern = _normalize_label(await field.get_attribute("pattern") or "")
    parsed = _parse_date(value)
    if not parsed:
        return value

    day, month, year = parsed
    if field_type == "date":
        return f"{year:04d}-{month:02d}-{day:02d}"
    if "tt" in placeholder or "dd mm" in pattern:
        return f"{day:02d}.{month:02d}.{year:04d}"
    if "mm dd" in pattern:
        return f"{month:02d}/{day:02d}/{year:04d}"
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


def _env_value(env_name: str, applicant_profile: ApplicantProfile | None = None) -> str:
    if env_name in {
        "APPLICANT_DATE_OF_BIRTH_DAY",
        "APPLICANT_DATE_OF_BIRTH_MONTH",
        "APPLICANT_DATE_OF_BIRTH_YEAR",
    }:
        parsed = _parse_date(os.getenv(_profile_env_name("APPLICANT_DATE_OF_BIRTH", applicant_profile), "").strip())
        if not parsed:
            return ""
        day, month, year = parsed
        if env_name == "APPLICANT_DATE_OF_BIRTH_DAY":
            return f"{day:02d}"
        if env_name == "APPLICANT_DATE_OF_BIRTH_MONTH":
            return f"{month:02d}"
        return f"{year:04d}"
    return os.getenv(_profile_env_name(env_name, applicant_profile), "").strip()


def _profile_env_name(env_name: str, applicant_profile: ApplicantProfile | None = None) -> str:
    if applicant_profile is None or applicant_profile.env_prefix == "APPLICANT":
        return env_name
    if not env_name.startswith("APPLICANT_"):
        return env_name
    return f"{applicant_profile.env_prefix}_{env_name.removeprefix('APPLICANT_')}"
