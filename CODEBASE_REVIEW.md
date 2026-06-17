# Codebase Review And Error Investigation

## Project Purpose

This project monitors the StaedteRegion Aachen appointment website with Playwright and books a configured visa appointment when a matching slot appears. The current config targets RWTH Studenten appointments at the RWTH Super C location for September and October 2026, with `dry_run: true` as the safe public default.

## Runtime Flow

1. `src/main.py` loads config, creates the logger, checks the duplicate-booking success flag, starts a Playwright browser page, and runs the monitor until the daily stop time.
2. `src/browser.py` creates Chromium with German locale, Europe/Berlin timezone, and a desktop viewport.
3. `src/monitor.py` drives the site to Step 4, repeatedly checks the calendar, handles recoverable navigation failures, and calls `book_slot()` when `check_target_slot()` returns a target slot.
4. `src/slot_checker.py` reads the visible calendar, navigates months, expands target date rows, detects time controls, and returns a `SlotCandidate`.
5. `src/booker.py` selects the appointment time, advances through any confirmation step, fills Step 5 applicant details, and either stops in dry-run mode or submits the final booking.
6. `src/form_filler.py` maps visible Step 5 labels to `.env` applicant fields, validates required values, fills inputs/selects/checkboxes, and waits for manual security text when needed.
7. `src/state.py` writes `confirmations/booking_success.flag` after a successful real booking and blocks future duplicate runs.

## Support Modules

- `src/logger.py` writes console logs and `logs/monitor.log`.
- `src/page_utils.py` centralizes Playwright click retry and page readiness waits.
- `src/email_notifier.py` sends booking and heartbeat emails when SMTP is configured.
- `src/smtp_test.py` validates SMTP settings.
- `src/telegram_notifier.py` sends optional Telegram alerts with throttling.
- `src/telegram_setup.py` prints Telegram chat IDs after the user messages the bot.
- `src/manual_challenge.py` detects manual CAPTCHA pages, saves screenshots, creates a phone-control request, and waits for the user to clear the challenge.
- `src/manual_action_state.py`, `src/security_state.py`, and `src/phone_alerts.py` store phone-control JSON state under `logs/`.
- `src/control_server.py` exposes a small authenticated phone dashboard for starting/stopping the bot, viewing logs, seeing screenshots, and submitting manual security answers.

## Tests And Deployment

- `tests/test_control_server.py` covers immediate bot-process startup failure handling.
- `tests/test_form_filler.py` covers security label mapping and split birth-date assignment.
- `tests/test_slot_checker.py` covers time extraction and slot-choice prioritization.
- `deploy/start-control-server.ps1` starts the phone control server.
- `deploy/install-phone-control-task.ps1` creates or updates `.env`, registers a Windows startup task or Startup-folder launcher, and optionally adds a firewall rule.
- `deploy/uninstall-phone-control-task.ps1` removes the Windows phone-control startup integration.
- `deploy/visa-monitor.service.example` is a Linux systemd service template.

## Observed Error

The live logs show no Python traceback. The actual failure is behavioral:

```text
valid_target_slot_detected | detail='2026-08-19 08:30 | ... | 08:30 08:30 08:30'
Selecting candidate slot: Mittwoch, 19.08.2026 ... | 08:30 08:30 08:30
Clicking stored appointment time point: 2026-08-19 08:30
Clicking appointment date row: 2026-08-19
slot_selection_unavailable | No visible appointment time button was available for 2026-08-19
```

## Root Cause

The scanner correctly expands the August 19 date row and detects an available `08:30` slot, but the rendered page exposes the appointment time as repeated visible text, for example `08:30 08:30 08:30`.

`src/booker.py` then tries `_click_visible_time_control()` before using stored coordinates. That function only accepted controls whose entire visible text was exactly one time, such as `08:30`. It rejected the real repeated-text control, fell back to a stored coordinate click, failed to advance, clicked the date row again, and finally reported `SlotSelectionUnavailable`.

## Fix Strategy

The fix is to treat repeated copies of the same preferred time as a valid concrete time control when there is no date text or range separator. That lets the booking step click the live DOM control it can still see, instead of relying first on stale or broad coordinates from the scan step.
