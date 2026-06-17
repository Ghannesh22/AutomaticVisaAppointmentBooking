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
    click_x: float | None = None
    click_y: float | None = None
    date_click_x: float | None = None
    date_click_y: float | None = None


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


async def find_target_slot(
    page: Page,
    config: AppConfig,
    logger: Logger,
    target_months: tuple[str, ...] | None = None,
) -> SlotCandidate | None:
    return (await check_target_slot(page, config, logger, target_months)).slot


async def find_july_slot(page: Page, config: AppConfig, logger: Logger) -> SlotCandidate | None:
    return await find_target_slot(page, config, logger)


async def check_july_slot(page: Page, config: AppConfig, logger: Logger) -> SlotCheckResult:
    return await check_target_slot(page, config, logger)


async def check_target_slot(
    page: Page,
    config: AppConfig,
    logger: Logger,
    target_months: tuple[str, ...] | None = None,
) -> SlotCheckResult:
    target_months = target_months or config.target_months
    observations: list[MonthSlotObservation] = []
    outside_slot_text: str | None = None
    outside_month_observations: dict[str, MonthSlotObservation] = {}
    last_checked_at = datetime.now()
    last_snippet = "No visible page text"
    last_page_text = ""
    last_visible_month_key: str | None = None
    first_target_month = min(target_months)
    final_target_month = max(target_months)

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

        observation = await _observe_visible_month(page, config, page_text, target_months)
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
                _target_month_visible(page_text, target_months),
                visible_month,
            )
            if not parsed_date:
                continue

            month = _month_key(parsed_date.year, parsed_date.month)
            if month not in target_months:
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

        if visible_month_key in target_months:
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
    if last_visible_month_key in target_months:
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


def _target_month_visible(text: str, target_months: tuple[str, ...]) -> bool:
    return any(_month_visible(text, month) for month in target_months)


def _month_visible(text: str, month_key: str) -> bool:
    year, month_number = month_key.split("-")
    month_name = next(
        name
        for name, number in MONTHS_DE.items()
        if number == int(month_number) and "ae" not in name
    )
    return month_name in text and year in text


async def _observe_visible_month(
    page: Page,
    config: AppConfig,
    page_text: str,
    target_months: tuple[str, ...] | None = None,
) -> MonthSlotObservation | None:
    target_months = target_months or config.target_months
    visible_month = _first_visible_month(page_text)
    if not visible_month:
        return None

    year, month = visible_month
    visible_month_key = _month_key(year, month)
    target_month = visible_month_key in target_months
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
            const rect = el.getBoundingClientRect();
            const unavailablePattern = /disabled|unavailable|nicht|belegt|rot/i;
            let disabled = false;
            let unavailable = false;
            for (let node = el; node && node !== document.body; node = node.parentElement) {
                const style = window.getComputedStyle(node);
                disabled = disabled || node.disabled || node.getAttribute('aria-disabled') === 'true';
                unavailable = unavailable
                    || unavailablePattern.test(node.className || '')
                    || style.visibility === 'hidden'
                    || style.display === 'none';
            }
            const style = window.getComputedStyle(el);
            const visible = style.visibility !== 'hidden' && style.display !== 'none'
                && rect.width > 0 && rect.height > 0;
            return !disabled && !unavailable && visible;
        }"""
    )


async def _is_available_time_control(handle: ElementHandle) -> bool:
    return await handle.evaluate(
        """el => {
            const unavailablePattern = /disabled|inactive|unavailable|nicht|belegt|gebucht|ausgebucht|deaktiviert|rot|grau|grey|gray/i;
            const parseRgb = value => {
                const match = String(value || "").match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)(?:,\\s*([\\d.]+))?\\)/i);
                if (!match) {
                    return null;
                }
                return {
                    r: Number(match[1]),
                    g: Number(match[2]),
                    b: Number(match[3]),
                    a: match[4] === undefined ? 1 : Number(match[4]),
                };
            };
            const luminance = color => color ? (0.2126 * color.r + 0.7152 * color.g + 0.0722 * color.b) : 255;
            const isInteractive = node => {
                const tag = node.tagName;
                return tag === "A" || tag === "BUTTON" || tag === "INPUT" || tag === "LABEL"
                    || node.getAttribute("role") === "button"
                    || node.hasAttribute("onclick")
                    || node.hasAttribute("tabindex");
            };
            let interactive = false;
            let fg = null;
            let bg = null;
            for (let node = el; node && node !== document.body; node = node.parentElement) {
                const style = window.getComputedStyle(node);
                const rect = node.getBoundingClientRect();
                if (
                    node.hidden
                    || node.disabled
                    || node.getAttribute("aria-disabled") === "true"
                    || node.getAttribute("aria-hidden") === "true"
                    || style.display === "none"
                    || style.visibility === "hidden"
                    || style.pointerEvents === "none"
                    || Number(style.opacity || "1") < 0.75
                    || unavailablePattern.test(node.className || "")
                    || unavailablePattern.test(node.getAttribute("aria-label") || "")
                    || unavailablePattern.test(node.getAttribute("title") || "")
                ) {
                    return false;
                }
                if (rect.width > 0 && rect.height > 0 && isInteractive(node)) {
                    interactive = true;
                }
                if (!fg) {
                    fg = parseRgb(style.color);
                }
                const candidateBg = parseRgb(style.backgroundColor);
                if (!bg && candidateBg && candidateBg.a > 0.1) {
                    bg = candidateBg;
                }
            }
            if (!interactive) {
                return false;
            }
            if (fg && bg && Math.abs(luminance(fg) - luminance(bg)) < 55) {
                return false;
            }
            return true;
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
    scrubbed = _remove_date_text(text)
    match = re.search(r"\b([01]?\d|2[0-3]):[0-5]\d\b", scrubbed)
    if match:
        return match.group(0)

    match = re.search(
        r"\b([01]?\d|2[0-3])\.[0-5]\d\b(?=\s*(?:uhr\b|$))",
        scrubbed,
        flags=re.IGNORECASE,
    )
    return match.group(0).replace(".", ":") if match else None


def _remove_date_text(text: str) -> str:
    without_numeric_dates = re.sub(r"\b\d{1,2}\.\d{1,2}\.\d{4}\b", " ", text)
    without_iso_dates = re.sub(r"\b\d{4}-\d{1,2}-\d{1,2}\b", " ", without_numeric_dates)
    return re.sub(
        r"\b\d{1,2}\.\s*[a-zA-Z\u00c4\u00d6\u00dc\u00e4\u00f6\u00fcÃƒÂ¤ÃƒÂ¶ÃƒÂ¼]+\s+\d{4}\b",
        " ",
        without_iso_dates,
        flags=re.IGNORECASE,
    )


async def _slot_from_expanded_date_group(
    page: Page,
    date_handle: ElementHandle,
    parsed_date: date,
    date_text: str,
    logger: Logger,
) -> SlotCandidate | None:
    logger.info("Target date row found without a time; expanding date group: %s", date_text[:120])
    await _click_date_group_expand_control(page, date_handle, logger)
    await page.wait_for_timeout(700)
    slot = await _find_expanded_time_slot(page, parsed_date, date_text, logger)
    if slot:
        return await _with_date_click_target(slot, date_handle)

    logger.info("No concrete appointment time after expand-control click; trying normal date-row click")
    await click_element_with_retry(date_handle, page, logger, "target date row")
    await page.wait_for_timeout(700)
    slot = await _find_expanded_time_slot(page, parsed_date, date_text, logger)
    if slot:
        return await _with_date_click_target(slot, date_handle)

    logger.info("Expanded target date row, but no concrete appointment time was visible")
    return None


async def _find_expanded_time_slot(
    page: Page,
    parsed_date: date,
    date_text: str,
    logger: Logger,
) -> SlotCandidate | None:
    handles = await page.locator(
        "a, button, input[type=button], input[type=submit], label, span, div, "
        "[role=button], [onclick], [tabindex]"
    ).element_handles()
    choices: list[tuple[int, int, ElementHandle, str, str, dict[str, float] | None]] = []
    for handle in handles:
        if not await _is_candidate_enabled(handle):
            continue
        text = await _element_text(handle)
        time_text = _extract_time(text)
        if not time_text:
            continue
        priority = _time_choice_priority(text, time_text)
        if priority >= 4:
            logger.info("Ignoring appointment range/container after expanding date row: %s", text[:80])
            continue
        if not await _is_available_time_control(handle):
            logger.info("Ignoring unavailable/greyed appointment time after expanding date row: %s", text[:80])
            continue
        logger.info("Concrete visible appointment time found after expanding date row: %s", time_text)
        choices.append(
            (priority, len(text), handle, text, time_text, await _element_click_point(handle))
        )

    if not choices:
        return None

    _, _, handle, text, time_text, click_point = sorted(choices, key=lambda choice: (choice[0], choice[1]))[0]
    return SlotCandidate(
        element=handle,
        date_text=parsed_date.isoformat(),
        time_text=time_text,
        raw_text=f"{date_text} | {text}",
        click_x=click_point["x"] if click_point else None,
        click_y=click_point["y"] if click_point else None,
    )


async def _with_date_click_target(slot: SlotCandidate, date_handle: ElementHandle) -> SlotCandidate:
    click_point = await _element_click_point(date_handle)
    if click_point:
        slot.date_click_x = click_point["x"]
        slot.date_click_y = click_point["y"]
    return slot


def _time_choice_priority(text: str, time_text: str) -> int:
    scrubbed = re.sub(r"\s+", " ", _remove_date_text(text)).strip()
    normalized = scrubbed.replace(".", ":")
    if re.fullmatch(r"(?:[01]?\d|2[0-3]):[0-5]\d", normalized):
        return 0
    if _contains_date_text(text):
        return 5
    if "-" in normalized:
        return 4
    if re.fullmatch(r"(?:[01]?\d|2[0-3]):[0-5]\d\s*-\s*(?:[01]?\d|2[0-3]):[0-5]\d", normalized):
        return 4
    if time_text in normalized:
        return 1
    return 5


async def _element_click_point(handle: ElementHandle) -> dict[str, float] | None:
    return await handle.evaluate(
        """el => {
            const rect = el.getBoundingClientRect();
            if (rect.width <= 0 || rect.height <= 0) {
                return null;
            }
            return {
                x: rect.left + rect.width / 2,
                y: rect.top + rect.height / 2,
            };
        }"""
    )


async def _click_date_group_expand_control(page: Page, date_handle: ElementHandle, logger: Logger) -> None:
    click_point = await date_handle.evaluate(
        """el => {
            const normalize = value => (value || "").replace(/\\s+/g, " ").trim();
            const ownText = normalize(el.innerText || el.textContent || el.value);
            let best = el;
            let bestRect = el.getBoundingClientRect();
            for (let node = el; node && node !== document.body; node = node.parentElement) {
                const rect = node.getBoundingClientRect();
                const text = normalize(node.innerText || node.textContent || node.value);
                if (!text || rect.width <= 0 || rect.height <= 0) {
                    continue;
                }
                const sameDateRow = text === ownText || (ownText && text.includes(ownText));
                const notTooBroad = text.length <= Math.max(ownText.length + 80, 120);
                if (sameDateRow && notTooBroad && rect.width >= bestRect.width) {
                    best = node;
                    bestRect = rect;
                }
            }
            return {
                x: bestRect.left + Math.min(18, Math.max(6, bestRect.width * 0.08)),
                y: bestRect.top + bestRect.height / 2,
            };
        }"""
    )
    try:
        logger.info("Clicking target date row expand control")
        await page.mouse.click(click_point["x"], click_point["y"])
    except Exception as exc:
        logger.info("Date row expand-control click failed; falling back to row click: %s", exc)
        await click_element_with_retry(date_handle, page, logger, "target date row")


def _contains_date_text(text: str) -> bool:
    return _remove_date_text(text) != text


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
