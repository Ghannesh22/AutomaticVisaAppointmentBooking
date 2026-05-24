from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from logging import Logger

from playwright.async_api import ElementHandle, Page

from src.config_loader import AppConfig
from src.page_utils import click_element_with_retry, click_locator_with_retry


MONTHS_DE = {
    "januar": 1,
    "februar": 2,
    "märz": 3,
    "maerz": 3,
    "m\u00e4rz": 3,
    "april": 4,
    "mai": 5,
    "juni": 6,
    "juli": 7,
    "august": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "dezember": 12,
}


@dataclass
class SlotCandidate:
    element: ElementHandle
    date_text: str
    time_text: str
    raw_text: str


@dataclass
class MonthSlotObservation:
    month: str
    target_month: bool
    status: str
    detail: str | None = None


@dataclass
class SlotCheckResult:
    status: str
    checked_at: datetime
    snippet: str
    slot: SlotCandidate | None = None
    outside_slot_text: str | None = None
    month_observations: list[MonthSlotObservation] = field(default_factory=list)


async def find_target_slot(page: Page, config: AppConfig, logger: Logger) -> SlotCandidate | None:
    return (await check_target_slot(page, config, logger)).slot


async def find_july_slot(page: Page, config: AppConfig, logger: Logger) -> SlotCandidate | None:
    return await find_target_slot(page, config, logger)


async def check_july_slot(page: Page, config: AppConfig, logger: Logger) -> SlotCheckResult:
    return await check_target_slot(page, config, logger)


async def check_target_slot(page: Page, config: AppConfig, logger: Logger) -> SlotCheckResult:
    observations: list[MonthSlotObservation] = []
    outside_slot_text: str | None = None
    outside_month_observations: dict[str, MonthSlotObservation] = {}
    last_checked_at = datetime.now()
    last_snippet = "No visible page text"
    last_page_text = ""
    last_visible_month_key: str | None = None
    first_target_month = min(config.target_months)
    final_target_month = max(config.target_months)

    for _ in range(30):
        raw_page_text = await page.locator("body").inner_text()
        page_text = raw_page_text.lower()
        last_page_text = raw_page_text
        last_checked_at = datetime.now()
        last_snippet = _page_snippet(raw_page_text)
        visible_month = _first_visible_month(raw_page_text)
        visible_month_key = _month_key(*visible_month) if visible_month else None
        last_visible_month_key = visible_month_key

        if visible_month_key and visible_month_key > first_target_month:
            previous_button = page.locator(
                "a[title*='Zurück'], a[aria-label*='Zurück'], "
                "button[title*='Zurück'], button[aria-label*='Zurück'], "
                ".ui-datepicker-prev, [data-handler='prev']"
            ).first
            if await previous_button.count() and await previous_button.is_visible():
                logger.info(
                    "Calendar is past first target month %s; clicking previous-month control",
                    first_target_month,
                )
                await click_locator_with_retry(previous_button, page, logger, "previous-month control")
                continue

        observation = await _observe_visible_month(page, config, page_text)
        if observation:
            observations.append(observation)
            _log_month_slot_state(logger, observation, last_checked_at, last_snippet)

        handles = await page.locator(
            "a, button, input[type=button], input[type=submit], td[role=gridcell], "
            "[role=button], .ui-state-default, .ui-datepicker-calendar td"
        ).element_handles()

        for handle in handles:
            if not await _is_candidate_enabled(handle):
                continue
            text = await _element_text(handle)
            parsed_date = _extract_date(
                text,
                config,
                _target_month_visible(page_text, config),
                visible_month,
            )
            if not parsed_date:
                continue

            month = _month_key(parsed_date.year, parsed_date.month)
            if month not in config.target_months:
                if not outside_slot_text:
                    outside_slot_text = text[:160]
                if month not in outside_month_observations:
                    outside_month_observations[month] = MonthSlotObservation(
                        month=month,
                        target_month=False,
                        status="slots_available",
                        detail=text[:160],
                    )
                continue

            time_text = _extract_time(text)
            if not time_text:
                slot = await _slot_from_expanded_date_group(page, handle, parsed_date, text, logger)
                if slot:
                    detail = f"{parsed_date.isoformat()} {slot.time_text} | {slot.raw_text[:160]}"
                    _log_slot_state(
                        logger,
                        "valid_target_slot_detected",
                        last_checked_at,
                        last_snippet,
                        detail,
                    )
                    observations.extend(outside_month_observations.values())
                    observations.append(
                        MonthSlotObservation(
                            month=month,
                            target_month=True,
                            status="slots_available",
                            detail=detail,
                        )
                    )
                    return SlotCheckResult(
                        status="valid_target_slot_detected",
                        checked_at=last_checked_at,
                        snippet=last_snippet,
                        slot=slot,
                        month_observations=observations,
                    )
                continue

            slot = SlotCandidate(
                element=handle,
                date_text=parsed_date.isoformat(),
                time_text=time_text,
                raw_text=text,
            )
            detail = f"{parsed_date.isoformat()} {time_text} | {text[:160]}"
            _log_slot_state(
                logger,
                "valid_target_slot_detected",
                last_checked_at,
                last_snippet,
                detail,
            )
            observations.extend(outside_month_observations.values())
            observations.append(
                MonthSlotObservation(
                    month=month,
                    target_month=True,
                    status="slots_available",
                    detail=detail,
                )
            )
            return SlotCheckResult(
                status="valid_target_slot_detected",
                checked_at=last_checked_at,
                snippet=last_snippet,
                slot=slot,
                month_observations=observations,
            )

        if visible_month_key in config.target_months:
            logger.info("Checked target month %s", visible_month_key)
        if visible_month_key and visible_month_key >= final_target_month:
            break

        next_button = page.locator(
            "a[title*='Weiter'], a[aria-label*='Weiter'], "
            "button[title*='Weiter'], button[aria-label*='Weiter'], "
            ".ui-datepicker-next, [data-handler='next']"
        ).first
        if await next_button.count() == 0:
            break
        if not await next_button.is_visible():
            break

        logger.info(
            "Target month not complete yet; clicking next-month control toward %s",
            final_target_month,
        )
        await click_locator_with_retry(next_button, page, logger, "next-month control")

    observations.extend(outside_month_observations.values())
    if outside_slot_text:
        _log_slot_state(logger, "slot_detected_outside_target_months", last_checked_at, last_snippet, outside_slot_text)
        return SlotCheckResult(
            status="slot_detected_outside_target_months",
            checked_at=last_checked_at,
            snippet=last_snippet,
            outside_slot_text=outside_slot_text,
            month_observations=observations,
        )

    status = _empty_status(last_page_text)
    if last_visible_month_key in config.target_months:
        observations.append(
            MonthSlotObservation(
                month=last_visible_month_key,
                target_month=True,
                status=status,
            )
        )
    _log_slot_state(logger, status, last_checked_at, last_snippet)
    return SlotCheckResult(
        status=status,
        checked_at=last_checked_at,
        snippet=last_snippet,
        month_observations=observations,
    )


def _target_month_visible(text: str, config: AppConfig) -> bool:
    return any(_month_visible(text, month) for month in config.target_months)


def _month_visible(text: str, month_key: str) -> bool:
    year, month_number = month_key.split("-")
    month_name = next(
        name
        for name, number in MONTHS_DE.items()
        if number == int(month_number) and "ae" not in name
    )
    return month_name in text and year in text


async def _observe_visible_month(
    page: Page, config: AppConfig, page_text: str
) -> MonthSlotObservation | None:
    visible_month = _first_visible_month(page_text)
    if not visible_month:
        return None

    year, month = visible_month
    visible_month_key = _month_key(year, month)
    target_month = visible_month_key in config.target_months
    handles = await page.locator(
        "a, button, input[type=button], input[type=submit], td[role=gridcell], "
        "[role=button], .ui-state-default, .ui-datepicker-calendar td"
    ).element_handles()

    for handle in handles:
        if not await _is_candidate_enabled(handle):
            continue
        text = await _element_text(handle)
        parsed_date = _extract_date(text, config, default_month=visible_month)
        if parsed_date and parsed_date.year == year and parsed_date.month == month:
            return MonthSlotObservation(
                month=visible_month_key,
                target_month=target_month,
                status="slots_available",
                detail=text[:160],
            )

    return MonthSlotObservation(
        month=visible_month_key,
        target_month=target_month,
        status=_empty_status(page_text),
    )


def _first_visible_month(text: str) -> tuple[int, int] | None:
    normalized = text.lower()
    for month_name, year in re.findall(r"\b([a-z\u00e4\u00f6\u00fcÃ¤Ã¶Ã¼]+)\s+(\d{4})\b", normalized):
        month = MONTHS_DE.get(month_name)
        if month:
            return int(year), month
    return None


def _month_key(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


async def _is_candidate_enabled(handle: ElementHandle) -> bool:
    return await handle.evaluate(
        """el => {
            const style = window.getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            const disabled = el.disabled || el.getAttribute('aria-disabled') === 'true';
            const unavailable = /disabled|unavailable|nicht|belegt|rot/i.test(el.className || '');
            const visible = style.visibility !== 'hidden' && style.display !== 'none'
                && rect.width > 0 && rect.height > 0;
            return !disabled && !unavailable && visible;
        }"""
    )


async def _element_text(handle: ElementHandle) -> str:
    return await handle.evaluate(
        """el => [
            el.innerText,
            el.textContent,
            el.value,
            el.getAttribute('aria-label'),
            el.getAttribute('title')
        ].filter(Boolean).join(' ').replace(/\\s+/g, ' ').trim()"""
    )


def _extract_date(
    text: str,
    config: AppConfig,
    target_month_visible: bool = False,
    default_month: tuple[int, int] | None = None,
) -> date | None:
    normalized = text.lower().replace(",", " ")

    for day, month, year in re.findall(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b", normalized):
        return _safe_date(int(year), int(month), int(day))

    for year, month, day in re.findall(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", normalized):
        return _safe_date(int(year), int(month), int(day))

    for day, month_name, year in re.findall(
        r"\b(\d{1,2})\.\s*([a-zäöü]+)\s+(\d{4})\b",
        normalized,
    ):
        month = MONTHS_DE.get(month_name)
        if month:
            return _safe_date(int(year), month, int(day))

    calendar_day = re.fullmatch(r"\s*(\d{1,2})\s*", normalized)
    if calendar_day and (target_month_visible or default_month):
        year, month = default_month or (config.target_year, config.target_month_number)
        return _safe_date(year, month, int(calendar_day.group(1)))

    return None


def _extract_time(text: str) -> str | None:
    match = re.search(r"\b([01]?\d|2[0-3])[:.][0-5]\d\b", text)
    return match.group(0).replace(".", ":") if match else None


async def _slot_from_expanded_date_group(
    page: Page,
    date_handle: ElementHandle,
    parsed_date: date,
    date_text: str,
    logger: Logger,
) -> SlotCandidate | None:
    logger.info("Target date row found without a time; expanding date group: %s", date_text[:120])
    await click_element_with_retry(date_handle, page, logger, "target date row")
    await page.wait_for_timeout(500)

    handles = await page.locator(
        "a, button, input[type=button], input[type=submit], label, "
        "[role=button], [onclick], [tabindex]"
    ).element_handles()
    for handle in handles:
        if not await _is_candidate_enabled(handle):
            continue
        text = await _element_text(handle)
        time_text = _extract_time(text)
        if not time_text:
            continue
        logger.info("Concrete appointment time found after expanding date row: %s", time_text)
        return SlotCandidate(
            element=handle,
            date_text=parsed_date.isoformat(),
            time_text=time_text,
            raw_text=f"{date_text} | {text}",
        )

    logger.info("Expanded target date row, but no concrete appointment time was visible")
    return None


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _empty_status(page_text: str) -> str:
    lowered = page_text.lower()
    no_slot_markers = [
        "keine termine",
        "keine freien termine",
        "kein freier termin",
        "keine terminvorschläge",
        "keine freien zeiten",
        "ausgebucht",
        "derzeit keine",
    ]
    if any(marker in lowered for marker in no_slot_markers):
        return "no_slots_available"
    if "schritt 4" in lowered or "terminauswahl" in lowered or "terminvorschläge" in lowered:
        return "calendar_loaded_but_empty"
    return "no_slots_available"


def _page_snippet(page_text: str, max_length: int = 320) -> str:
    safe_lines = []
    for line in page_text.splitlines():
        clean = re.sub(r"\s+", " ", line).strip()
        if not clean:
            continue
        if _looks_private(clean):
            continue
        safe_lines.append(clean)
        if len(" | ".join(safe_lines)) >= max_length:
            break
    return " | ".join(safe_lines)[:max_length] or "No visible page text"


def _looks_private(text: str) -> bool:
    private_markers = [
        "vorname",
        "nachname",
        "familienname",
        "geburtsdatum",
        "e-mail",
        "email",
        "telefon",
        "reisepass",
        "passnummer",
    ]
    lowered = text.lower()
    return any(marker in lowered for marker in private_markers)


def _log_slot_state(
    logger: Logger,
    status: str,
    checked_at: datetime,
    snippet: str,
    detail: str | None = None,
) -> None:
    message = (
        "slot_state | checked_at=%s | result=%s | page='%s'"
        % (checked_at.isoformat(timespec="seconds"), status, snippet)
    )
    if detail:
        message += " | detail='%s'" % detail
    logger.info(message)


def _log_month_slot_state(
    logger: Logger,
    observation: MonthSlotObservation,
    checked_at: datetime,
    snippet: str,
) -> None:
    message = (
        "month_slot_state | checked_at=%s | month=%s | target_month=%s | result=%s | page='%s'"
        % (
            checked_at.isoformat(timespec="seconds"),
            observation.month,
            str(observation.target_month).lower(),
            observation.status,
            snippet,
        )
    )
    if observation.detail:
        message += " | detail='%s'" % observation.detail
    logger.info(message)
