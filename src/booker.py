from __future__ import annotations

import asyncio
from logging import Logger
from pathlib import Path

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from src.config_loader import AppConfig, ApplicantProfile, PROJECT_ROOT
from src.email_notifier import send_booking_email, smtp_enabled
from src.form_filler import fill_personal_details
from src.manual_challenge import wait_for_manual_captcha_if_present
from src.page_utils import click_locator_with_retry, wait_for_page_ready
from src.slot_checker import SlotCandidate
from src.state import has_success_flag, success_flag_path, write_success_flag
from src.telegram_notifier import send_telegram_alert


class SlotSelectionUnavailable(RuntimeError):
    pass


async def book_slot(
    page: Page,
    config: AppConfig,
    slot: SlotCandidate,
    logger: Logger,
    applicant_profile: ApplicantProfile | None = None,
) -> bool:
    profile_name = applicant_profile.name if applicant_profile else "default"
    if has_success_flag(profile_name):
        logger.warning(
            "Success flag already exists at %s for applicant profile '%s'; refusing duplicate booking",
            success_flag_path(profile_name),
            profile_name,
        )
        return True

    logger.info("Selecting candidate slot for applicant profile '%s': %s", profile_name, slot.raw_text)
    await _click_appointment_time(page, slot, logger)
    await wait_for_manual_captcha_if_present(page, logger, "after appointment slot selection")

    try:
        await _advance_after_slot_selection(page, logger)
    except PlaywrightTimeoutError as exc:
        raise SlotSelectionUnavailable(
            f"Appointment time {slot.date_text} {slot.time_text} did not advance to the next step"
        ) from exc
    await wait_for_manual_captcha_if_present(page, logger, "before applicant details")
    try:
        await fill_personal_details(page, logger, applicant_profile)
    except (PlaywrightTimeoutError, PlaywrightError) as exc:
        if await _page_has_booking_success(page):
            logger.warning(
                "Step 5 interaction ended during navigation, but Step 6 success is visible; finalizing booking"
            )
            return await _finalize_successful_booking(
                page,
                config,
                slot,
                logger,
                profile_name,
            )
        raise
    await wait_for_manual_captcha_if_present(page, logger, "after applicant details")

    if config.dry_run:
        logger.info("Dry-run mode is enabled; stopping before Step 5 booking submission")
        return False

    if has_success_flag(profile_name):
        logger.warning(
            "Success flag appeared before final submit at %s for applicant profile '%s'; refusing duplicate submission",
            success_flag_path(profile_name),
            profile_name,
        )
        return True

    logger.info("Dry-run disabled; submitting final booking")
    await wait_for_manual_captcha_if_present(page, logger, "before final booking submit")
    await _click_final_submit(page, logger)
    await wait_for_page_ready(page, logger, "final booking submit", settle_ms=1500)

    return await _finalize_successful_booking(page, config, slot, logger, profile_name)


async def _finalize_successful_booking(
    page: Page,
    config: AppConfig,
    slot: SlotCandidate,
    logger: Logger,
    profile_name: str,
) -> bool:
    screenshot = _confirmation_artifact_path("booking_success", ".png")
    await page.screenshot(path=str(screenshot), full_page=True)
    confirmation_text = await page.locator("body").inner_text()
    confirmation_file = _write_confirmation(confirmation_text)
    reference = _extract_reference(confirmation_text)

    logger.info("Saved success screenshot to %s", screenshot)
    logger.info("Saved confirmation text to %s", confirmation_file)
    flag = write_success_flag(
        appointment_date=slot.date_text,
        appointment_time=slot.time_text,
        location=config.appointment_location,
        reference=reference,
        profile_name=profile_name,
    )
    logger.info("Created booking success flag at %s", flag)
    if smtp_enabled():
        send_booking_email(
            appointment_date=slot.date_text,
            appointment_time=slot.time_text,
            location=config.appointment_location,
            reference=reference,
            attachment=screenshot,
        )
        logger.info("Sent Gmail SMTP booking notification")
    else:
        logger.info("SMTP settings are empty; skipping email notification")
    _send_booking_success_telegram_alert(
        appointment_date=slot.date_text,
        appointment_time=slot.time_text,
        location=config.appointment_location,
        profile_name=profile_name,
        reference=reference,
        screenshot=screenshot,
        logger=logger,
    )
    return True


def _send_booking_success_telegram_alert(
    *,
    appointment_date: str,
    appointment_time: str,
    location: str,
    profile_name: str,
    reference: str | None,
    screenshot: Path,
    logger: Logger,
) -> None:
    lines = [
        "Appointment successful.",
        "",
        f"Appointment date: {appointment_date}",
        f"Appointment time: {appointment_time}",
        f"Location: {location}",
        f"Applicant profile: {profile_name}",
    ]
    if reference:
        lines.append(f"Reference: {reference}")
    lines.extend(
        [
            "",
            f"Confirmation screenshot: {screenshot}",
            "",
            "Check the official website email immediately in case it contains a confirmation link.",
        ]
    )
    if send_telegram_alert(
        title="Appointment successful",
        message="\n".join(lines),
        severity="success",
        screenshot_path=screenshot,
        force=True,
    ):
        logger.info("Telegram booking-success alert sent")


async def _click_continue(page: Page, logger: Logger) -> None:
    logger.info("Clicking Weiter")
    button = _continue_button(page)
    await click_locator_with_retry(button, page, logger, "Weiter", click_timeout_ms=10000)


async def _click_appointment_time(page: Page, slot: SlotCandidate, logger: Logger) -> None:
    if await _click_detected_time_element(page, slot, logger):
        return

    if await _click_visible_time_control(page, slot, logger):
        return

    if await _click_stored_time_point(page, slot, logger):
        await page.wait_for_timeout(700)
        if await _slot_selection_has_next_action(page):
            return

    if await _appointment_time_is_visible(page, slot.time_text):
        logger.info(
            "Appointment time %s is visible but did not select; skipping date-row click to avoid collapsing it",
            slot.time_text,
        )
        if await _click_detected_time_element(page, slot, logger):
            return
        if await _click_visible_time_control(page, slot, logger):
            return
        raise SlotSelectionUnavailable(
            f"Visible appointment time {slot.time_text} could not be selected for {slot.date_text}"
        )

    if await _click_date_row(page, slot, logger):
        await page.wait_for_timeout(700)
        if await _slot_selection_has_next_action(page):
            return
        if await _click_detected_time_element(page, slot, logger):
            return
        if await _click_visible_time_control(page, slot, logger):
            return

    raise SlotSelectionUnavailable(
        f"No visible appointment time button was available for {slot.date_text}"
    )


async def _click_detected_time_element(page: Page, slot: SlotCandidate, logger: Logger) -> bool:
    element = slot.element
    if element is None:
        return False

    if await _click_detected_time_element_by_dom(page, element, slot, logger):
        return True

    try:
        click_target = await element.evaluate(
            """(el, preferredTime) => {
                const normalize = value => (value || "").replace(/\\s+/g, " ").trim();
                const textOf = node => normalize([
                    node.innerText,
                    node.textContent,
                    node.value,
                    node.getAttribute && node.getAttribute("aria-label"),
                    node.getAttribute && node.getAttribute("title"),
                ].filter(Boolean).join(" "));
                const hasDate = text => /\\b(?:\\d{1,2}\\.\\d{1,2}\\.\\d{4}|\\d{4}-\\d{1,2}-\\d{1,2})\\b/.test(text);
                const isVisible = node => {
                    const style = window.getComputedStyle(node);
                    const rect = node.getBoundingClientRect();
                    return style.display !== "none"
                        && style.visibility !== "hidden"
                        && Number(style.opacity || "1") >= 0.75
                        && rect.width > 0
                        && rect.height > 0;
                };
                const isInteractive = node => {
                    const tag = node.tagName;
                    return tag === "A" || tag === "BUTTON" || tag === "INPUT" || tag === "LABEL"
                        || node.getAttribute("role") === "button"
                        || node.hasAttribute("onclick")
                        || node.hasAttribute("tabindex");
                };
                const exposedCenter = node => {
                    const rects = Array.from(node.getClientRects());
                    for (const rect of rects) {
                        if (rect.width <= 0 || rect.height <= 0) {
                            continue;
                        }
                        const x = rect.left + rect.width / 2;
                        const y = rect.top + rect.height / 2;
                        if (x < 0 || y < 0 || x > window.innerWidth || y > window.innerHeight) {
                            continue;
                        }
                        const hit = document.elementFromPoint(x, y);
                        if (hit && (hit === node || node.contains(hit) || hit.contains(node))) {
                            return { x, y };
                        }
                    }
                    return null;
                };

                const candidates = [];
                for (let node = el; node && node !== document.body; node = node.parentElement) {
                    if (!isVisible(node) || !isInteractive(node)) {
                        continue;
                    }
                    const text = textOf(node);
                    if (!text.includes(preferredTime) || hasDate(text)) {
                        continue;
                    }
                    const point = exposedCenter(node);
                    if (!point) {
                        continue;
                    }
                    candidates.push({ node, text, point });
                }
                candidates.sort((left, right) => left.text.length - right.text.length);

                const target = candidates[0] || null;
                if (!target) {
                    const text = textOf(el);
                    const point = exposedCenter(el);
                    if (!point || !text.includes(preferredTime) || hasDate(text)) {
                        return null;
                    }
                    el.scrollIntoView({ block: "center", inline: "center" });
                    return { text, x: point.x, y: point.y, canDomClick: false };
                }

                target.node.scrollIntoView({ block: "center", inline: "center" });
                const point = exposedCenter(target.node);
                if (!point) {
                    return null;
                }
                return {
                    text: target.text,
                    x: point.x,
                    y: point.y,
                    canDomClick: true,
                };
            }""",
            slot.time_text,
        )
    except (PlaywrightTimeoutError, PlaywrightError) as exc:
        logger.info(
            "Detected appointment time element is no longer usable: %s",
            str(exc).splitlines()[0][:220],
        )
        return False

    if not click_target:
        return False

    logger.info("Clicking detected appointment time element: %s", click_target["text"])
    await page.mouse.click(click_target["x"], click_target["y"])
    await wait_for_page_ready(page, logger, "detected appointment time element", settle_ms=300)
    if await _slot_selection_has_next_action(page):
        return True

    if click_target.get("canDomClick"):
        try:
            await element.evaluate(
                """(el, preferredTime) => {
                    const normalize = value => (value || "").replace(/\\s+/g, " ").trim();
                    const textOf = node => normalize([
                        node.innerText,
                        node.textContent,
                        node.value,
                        node.getAttribute && node.getAttribute("aria-label"),
                        node.getAttribute && node.getAttribute("title"),
                    ].filter(Boolean).join(" "));
                    const hasDate = text => /\\b(?:\\d{1,2}\\.\\d{1,2}\\.\\d{4}|\\d{4}-\\d{1,2}-\\d{1,2})\\b/.test(text);
                    const isInteractive = node => {
                        const tag = node.tagName;
                        return tag === "A" || tag === "BUTTON" || tag === "INPUT" || tag === "LABEL"
                            || node.getAttribute("role") === "button"
                            || node.hasAttribute("onclick")
                            || node.hasAttribute("tabindex");
                    };
                    let best = null;
                    for (let node = el; node && node !== document.body; node = node.parentElement) {
                        const text = textOf(node);
                        if (isInteractive(node) && text.includes(preferredTime) && !hasDate(text)) {
                            if (!best || text.length < textOf(best).length) {
                                best = node;
                            }
                        }
                    }
                    if (best) {
                        best.click();
                    }
                }""",
                slot.time_text,
            )
            await wait_for_page_ready(page, logger, "detected appointment time DOM click", settle_ms=300)
            return await _slot_selection_has_next_action(page)
        except (PlaywrightTimeoutError, PlaywrightError) as exc:
            logger.info(
                "Detected appointment time DOM click failed: %s",
                str(exc).splitlines()[0][:220],
            )

    return False


async def _click_detected_time_element_by_dom(page: Page, element, slot: SlotCandidate, logger: Logger) -> bool:
    try:
        click_result = await element.evaluate(
            """(el, preferredTime) => {
                const normalize = value => (value || "").replace(/\\s+/g, " ").trim();
                const textOf = node => normalize([
                    node.innerText,
                    node.textContent,
                    node.value,
                    node.getAttribute && node.getAttribute("aria-label"),
                    node.getAttribute && node.getAttribute("title"),
                ].filter(Boolean).join(" "));
                const hasDate = text => /\\b(?:\\d{1,2}\\.\\d{1,2}\\.\\d{4}|\\d{4}-\\d{1,2}-\\d{1,2})\\b/.test(text);
                const isDisabled = node => {
                    const style = window.getComputedStyle(node);
                    return node.disabled
                        || node.hidden
                        || node.getAttribute("aria-disabled") === "true"
                        || node.getAttribute("aria-hidden") === "true"
                        || style.display === "none"
                        || style.visibility === "hidden"
                        || Number(style.opacity || "1") < 0.5;
                };
                const isActionable = node => {
                    const tag = node.tagName;
                    return tag === "A"
                        || tag === "BUTTON"
                        || tag === "INPUT"
                        || tag === "LABEL"
                        || node.getAttribute("role") === "button"
                        || node.hasAttribute("onclick")
                        || node.hasAttribute("tabindex");
                };
                const summarize = node => ({
                    tag: node.tagName.toLowerCase(),
                    id: node.id || "",
                    name: node.getAttribute("name") || "",
                    type: node.getAttribute("type") || "",
                    text: textOf(node).slice(0, 120),
                    html: (node.outerHTML || "").replace(/\\s+/g, " ").slice(0, 500),
                });
                const candidates = [];
                const addCandidate = node => {
                    if (!node || isDisabled(node) || !isActionable(node)) {
                        return;
                    }
                    const text = textOf(node);
                    if (!text.includes(preferredTime) || hasDate(text)) {
                        return;
                    }
                    candidates.push(node);
                };

                addCandidate(el);
                for (let node = el; node && node !== document.body; node = node.parentElement) {
                    addCandidate(node);
                }
                for (const node of el.querySelectorAll?.("a, button, input, label, [role='button'], [onclick], [tabindex]") || []) {
                    addCandidate(node);
                }

                candidates.sort((left, right) => textOf(left).length - textOf(right).length);
                const target = candidates[0] || null;
                if (!target) {
                    return { clicked: false, reason: "no_actionable_time_element", detected: summarize(el) };
                }

                target.scrollIntoView({ block: "center", inline: "center" });
                const id = target.id || target.getAttribute("for");
                const associated = id ? document.getElementById(id) : null;
                const clickTargets = [];
                if (associated && !isDisabled(associated)) {
                    clickTargets.push(associated);
                }
                clickTargets.push(target);

                for (const clickTarget of clickTargets) {
                    clickTarget.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, view: window }));
                    clickTarget.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true, view: window }));
                    clickTarget.click();
                }
                return { clicked: true, target: summarize(target), detected: summarize(el) };
            }""",
            slot.time_text,
        )
    except (PlaywrightTimeoutError, PlaywrightError) as exc:
        logger.info(
            "Detected appointment time DOM click failed before execution: %s",
            str(exc).splitlines()[0][:220],
        )
        return False

    if not click_result or not click_result.get("clicked"):
        if click_result:
            logger.info(
                "Detected appointment time element had no actionable DOM click target: %s | html=%s",
                click_result.get("reason", "unknown"),
                click_result.get("detected", {}).get("html", ""),
            )
        return False

    target = click_result.get("target", {})
    logger.info(
        "Clicking detected appointment time DOM element: <%s id='%s' name='%s'> %s",
        target.get("tag", ""),
        target.get("id", ""),
        target.get("name", ""),
        target.get("text", ""),
    )
    await wait_for_page_ready(page, logger, "detected appointment time DOM element", settle_ms=500)
    return await _slot_selection_has_next_action(page)


async def _click_stored_time_point(page: Page, slot: SlotCandidate, logger: Logger) -> bool:
    if slot.click_x is None or slot.click_y is None:
        return False
    if "-" in slot.raw_text:
        return False

    try:
        logger.info("Clicking stored appointment time point: %s %s", slot.date_text, slot.time_text)
        await page.mouse.click(slot.click_x, slot.click_y)
        await wait_for_page_ready(page, logger, "stored appointment time point")
        return True
    except (PlaywrightTimeoutError, PlaywrightError) as exc:
        logger.info(
            "Stored appointment time point click failed: %s",
            str(exc).splitlines()[0][:220],
        )
        return False


async def _click_date_row(page: Page, slot: SlotCandidate, logger: Logger) -> bool:
    if slot.date_click_x is None or slot.date_click_y is None:
        return False

    try:
        logger.info("Clicking appointment date row: %s", slot.date_text)
        await page.mouse.click(slot.date_click_x, slot.date_click_y)
        return True
    except (PlaywrightTimeoutError, PlaywrightError) as exc:
        logger.info(
            "Appointment date-row coordinate click failed: %s",
            str(exc).splitlines()[0][:220],
        )

    return False


async def _appointment_time_is_visible(page: Page, time_text: str) -> bool:
    return await page.evaluate(
        """preferredTime => {
            const normalize = value => (value || "").replace(/\\s+/g, " ").trim();
            const hasDate = text => /\\b(?:\\d{1,2}\\.\\d{1,2}\\.\\d{4}|\\d{4}-\\d{1,2}-\\d{1,2})\\b/.test(text);
            return Array.from(document.querySelectorAll("body *")).some(el => {
                const style = window.getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                if (
                    style.display === "none"
                    || style.visibility === "hidden"
                    || Number(style.opacity || "1") < 0.75
                    || rect.width <= 0
                    || rect.height <= 0
                ) {
                    return false;
                }
                const text = normalize([
                    el.innerText,
                    el.textContent,
                    el.value,
                    el.getAttribute("aria-label"),
                    el.getAttribute("title"),
                ].filter(Boolean).join(" "));
                return text.includes(preferredTime) && !hasDate(text);
            });
        }""",
        time_text,
    )


async def _click_visible_time_control(page: Page, slot: SlotCandidate, logger: Logger) -> bool:
    click_point = await page.evaluate(
        """({ preferredTime }) => {
            const normalize = value => (value || "").replace(/\\s+/g, " ").trim();
            const canonicalTime = value => normalize(value).replace(".", ":");
            const timeOnly = /^(?:[01]?\\d|2[0-3])[:.][0-5]\\d$/;
            const dateText = /\\b(?:\\d{1,2}\\.\\d{1,2}\\.\\d{4}|\\d{4}-\\d{1,2}-\\d{1,2})\\b/;
            const controls = Array.from(document.querySelectorAll("body *"));
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
            const visibleText = el => normalize([
                el.innerText,
                el.value,
                el.getAttribute("aria-label"),
                el.getAttribute("title"),
            ].filter(Boolean).join(" "));
            const isUnavailable = el => {
                let interactive = false;
                let fg = null;
                let bg = null;
                for (let node = el; node && node !== document.body; node = node.parentElement) {
                    const style = window.getComputedStyle(node);
                    if (node.hidden || node.getAttribute("aria-hidden") === "true") {
                        return true;
                    }
                    if (node.disabled || node.getAttribute("aria-disabled") === "true") {
                        return true;
                    }
                    if (
                        style.display === "none"
                        || style.visibility === "hidden"
                        || Number(style.opacity || "1") < 0.75
                        || style.pointerEvents === "none"
                    ) {
                        return true;
                    }
                    if (
                        unavailablePattern.test(node.className || "")
                        || unavailablePattern.test(node.getAttribute("aria-label") || "")
                        || unavailablePattern.test(node.getAttribute("title") || "")
                    ) {
                        return true;
                    }
                    const rect = node.getBoundingClientRect();
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
                    return true;
                }
                return !!(fg && bg && Math.abs(luminance(fg) - luminance(bg)) < 55);
            };
            const exposedCenter = el => {
                const rects = Array.from(el.getClientRects());
                for (const rect of rects) {
                    if (rect.width <= 0 || rect.height <= 0) {
                        continue;
                    }
                    const x = rect.left + rect.width / 2;
                    const y = rect.top + rect.height / 2;
                    if (x < 0 || y < 0 || x > window.innerWidth || y > window.innerHeight) {
                        continue;
                    }
                    const hit = document.elementFromPoint(x, y);
                    if (hit && (hit === el || el.contains(hit) || hit.contains(el))) {
                        return { x, y };
                    }
                }
                return null;
            };
            const choices = [];
            for (const el of controls) {
                const style = window.getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                const visible = style.visibility !== "hidden" && style.display !== "none"
                    && rect.width > 0 && rect.height > 0;
                if (!visible || isUnavailable(el)) {
                    continue;
                }
                const clickPoint = exposedCenter(el);
                if (!clickPoint) {
                    continue;
                }

                const text = visibleText(el);
                if (text.length > 20) {
                    continue;
                }

                const canonical = canonicalTime(text);
                const tokens = canonical.split(" ").filter(Boolean);
                const repeatedPreferredTime = tokens.length > 1
                    && tokens.every(token => token === preferredTime);
                if (
                    dateText.test(text)
                    || text.includes("-")
                    || (!timeOnly.test(text) && !repeatedPreferredTime)
                ) {
                    continue;
                }

                choices.push({
                    priority: canonical === preferredTime || repeatedPreferredTime ? 0 : 1,
                    text,
                    target: el,
                    x: clickPoint.x,
                    y: clickPoint.y,
                });
            }
            choices.sort((left, right) => left.priority - right.priority || left.text.length - right.text.length);
            const choice = choices[0] || null;
            if (!choice) {
                return null;
            }
            choice.target.scrollIntoView({ block: "center", inline: "center" });
            const clickPoint = exposedCenter(choice.target);
            if (!clickPoint) {
                return null;
            }
            choice.target.click();
            return {
                priority: choice.priority,
                text: choice.text,
                x: clickPoint.x,
                y: clickPoint.y,
            };
        }""",
        {"preferredTime": slot.time_text},
    )
    if not click_point:
        return False

    logger.info("Clicking visible appointment time control: %s", click_point["text"])
    await wait_for_page_ready(page, logger, "visible appointment time DOM click")
    if await _slot_selection_has_next_action(page):
        return True

    logger.info("DOM click did not select appointment time; retrying with mouse coordinates")
    await page.mouse.click(click_point["x"], click_point["y"])
    await wait_for_page_ready(page, logger, "visible appointment time")
    return True


async def _advance_after_slot_selection(page: Page, logger: Logger) -> None:
    for _ in range(20):
        if await _has_visible_applicant_fields(page):
            logger.info("Slot selection reached applicant details form automatically")
            return

        if await _click_slot_confirmation_yes_if_present(page, logger):
            continue

        button = _continue_button(page)
        if await button.count() and await button.is_visible():
            await _click_continue(page, logger)
            return

        await asyncio.sleep(0.25)

    raise PlaywrightTimeoutError(
        "After selecting an appointment time, neither applicant fields nor Weiter became visible"
    )


async def _slot_selection_has_next_action(page: Page) -> bool:
    if await _has_visible_applicant_fields(page):
        return True

    button = _continue_button(page)
    if await button.count() and await button.is_visible():
        return True

    try:
        body_text = await page.locator("body").inner_text(timeout=1000)
    except PlaywrightTimeoutError:
        return False

    return _looks_like_slot_confirmation(body_text)


async def _has_visible_applicant_fields(page: Page) -> bool:
    fields = page.locator(
        "input:not([type=hidden]):not([type=submit]):not([type=button]), textarea, select"
    )
    count = await fields.count()
    for index in range(count):
        if await fields.nth(index).is_visible():
            return True
    return False


def _continue_button(page: Page):
    return page.locator(
        "#WeiterButton, input[value='Weiter'], button:has-text('Weiter'), "
        "input[type=submit][title='Weiter']"
    ).first


async def _click_slot_confirmation_yes_if_present(page: Page, logger: Logger) -> bool:
    try:
        body_text = await page.locator("body").inner_text(timeout=2000)
    except PlaywrightTimeoutError:
        return False
    normalized = body_text.lower().replace("ö", "oe")
    if not _looks_like_slot_confirmation(body_text):
        return False

    yes_button = page.locator(
        "input[value='Ja'], input[value='Yes'], input[type=submit][value*='Ja'], "
        "button:has-text('Ja'), button:has-text('Yes'), a:has-text('Ja'), a:has-text('Yes')"
    ).first
    if not await yes_button.count() or not await yes_button.is_visible():
        return False

    logger.info("Confirming selected appointment time with Ja")
    await click_locator_with_retry(yes_button, page, logger, "appointment confirmation Ja", click_timeout_ms=10000)
    return True


def _looks_like_slot_confirmation(text: str) -> bool:
    normalized = (
        text.lower()
        .replace("ö", "oe")
        .replace("Ã¶", "oe")
        .replace("ÃƒÂ¶", "oe")
    )
    return (
        "mit diesen eingaben fortfahren" in normalized
        or "continue with these entries" in normalized
        or "continue with this information" in normalized
    )


async def _click_final_submit(page: Page, logger: Logger) -> None:
    logger.info("Clicking final Book now/booking button")
    button = page.locator(
        "input[value='Book'], input[value*='Book now'], input[value*='Book appointment'], "
        "input[value*='Jetzt buchen'], input[value*='Termin buchen'], "
        "input[value*='Verbindlich buchen'], input[value*='Kostenpflichtig buchen'], "
        "input[value*='Reservieren'], input[value*='Buchen'], input[value*='Absenden'], "
        "button:has-text('Book'), button:has-text('Book now'), button:has-text('Book appointment'), "
        "button:has-text('Jetzt buchen'), button:has-text('Termin buchen'), "
        "button:has-text('Verbindlich buchen'), button:has-text('Kostenpflichtig buchen'), "
        "button:has-text('Reservieren'), button:has-text('Buchen'), button:has-text('Absenden'), "
        "a:has-text('Book'), a:has-text('Book now'), a:has-text('Jetzt buchen'), "
        "#WeiterButton, input[value='Weiter']"
    ).first
    await click_locator_with_retry(button, page, logger, "final Book now/booking button", click_timeout_ms=10000)


async def _page_has_booking_success(page: Page) -> bool:
    try:
        body_text = await page.locator("body").inner_text(timeout=3000)
    except (PlaywrightTimeoutError, PlaywrightError):
        return False
    return _looks_like_booking_success(body_text, page.url)


def _looks_like_booking_success(text: str, url: str = "") -> bool:
    import re
    import unicodedata

    without_marks = "".join(
        char
        for char in unicodedata.normalize("NFD", text.lower())
        if unicodedata.category(char) != "Mn"
    )
    normalized = re.sub(r"[^a-z0-9]+", " ", without_marks).strip()
    url = url.lower()
    success_markers = (
        "appointment successful",
        "successfully",
        "successful",
        "termin erfolgreich",
        "erfolgreich",
        "bestaetigung",
        "bestatigung",
        "confirmation",
    )
    email_markers = ("email", "e mail", "mail")
    has_success_text = any(marker in normalized for marker in success_markers)
    has_email_text = any(marker in normalized for marker in email_markers)
    return (
        "schritt 6" in normalized
        or ("reserve" in url and has_success_text)
        or (has_success_text and has_email_text)
    )


def _confirmation_artifact_path(prefix: str, suffix: str) -> Path:
    from datetime import datetime

    directory = PROJECT_ROOT / "confirmations"
    directory.mkdir(exist_ok=True)
    return directory / f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{suffix}"


def _write_confirmation(text: str) -> Path:
    from datetime import datetime

    directory = PROJECT_ROOT / "confirmations"
    directory.mkdir(exist_ok=True)
    path = directory / f"confirmation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    path.write_text(text, encoding="utf-8")
    return path


def _extract_reference(text: str) -> str | None:
    import re

    match = re.search(
        r"(?:Referenz|Vorgangsnummer|Bestätigungsnummer|Reservierungsnummer)\s*[:#]?\s*([A-Z0-9-]+)",
        text,
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else None
