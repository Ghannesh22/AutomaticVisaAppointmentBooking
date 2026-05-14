# Automatic Visa Appointment Booking

Small Windows-friendly Python project for monitoring the StädteRegion Aachen appointment website and booking any available July 2026 appointment for:

- Function unit: `Ausländer- und Staatsangehörigkeitsbehörde`
- Location: `RWTH - Außenstelle Super C`
- Category: `RWTH Studenten`
- Website: <https://termine.staedteregion-aachen.de/auslaenderamt/>

The project starts in visible browser mode with `dry_run: true`. Dry-run mode navigates the booking flow and stops before the final booking submission.

## Safety

- No CAPTCHA bypassing, queue bypassing, login bypassing, rate-limit evasion, or anti-bot circumvention.
- Monitoring uses `check_interval_seconds` from `config.yaml`.
- The script stops after a successful booking submission.
- After a successful real booking submission, the script creates `confirmations/booking_success.flag`.
- If that flag exists, future runs stop before monitoring or submitting anything.
- Runtime logs are written to `logs/monitor.log`.
- `.env`, logs, screenshots, traces, videos, and confirmation files are ignored by Git.
- Screenshots are saved only on errors or successful real booking.

## Setup On Windows

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m playwright install chromium
Copy-Item .env.example .env
```

If your Windows machine uses the Python launcher, `py` can be used instead of `python`.

Edit `.env` with applicant details and Gmail SMTP values. For Gmail, use an app password, not your normal account password.
Leave the SMTP settings empty to disable project email notifications; the official appointment email will still go to `APPLICANT_EMAIL`.

## Configuration

Edit `config.yaml`:

```yaml
check_interval_seconds: 300
max_runtime_minutes: 720
headless: false
dry_run: true
heartbeat_enabled: false
heartbeat_interval_minutes: 240
```

Keep `headless: false` while validating the flow. After dry-run validation, set:

```yaml
dry_run: false
```

Only use real booking mode after confirming the dry-run reaches the final overview page correctly.

## Run

```powershell
.\.venv\Scripts\Activate.ps1
python -m src.main
```

Stop monitoring with `Ctrl+C`. The browser is closed by Playwright cleanup and a final monitoring summary is written to `logs/monitor.log`.

## Booking Flow

The script performs this German website flow:

1. Opens the appointment website.
2. Clicks `Ausländer- und Staatsangehörigkeitsbehörde`.
3. Opens `RWTH - Außenstelle Super C`.
4. Increments `RWTH Studenten` to one applicant.
5. Clicks `Weiter`.
6. Accepts the information dialog with `OK`.
7. Clicks `Weiter` on the location/address page.
8. Monitors the appointment calendar page for July 2026.
9. Selects the first visible July 2026 slot.
10. Fills personal details from `.env`.
11. In dry-run mode, stops before final submission.
12. In real mode, submits the booking, sends a Gmail SMTP notification, and stops.

If the session expires or the navigation no longer matches the expected flow, the script logs the error, saves an error screenshot, and restarts from Step 1.
Repeated navigation failures use temporary exponential backoff before restarting the flow. The backoff resets after the calendar page loads successfully.

During dry-run monitoring, slot-state logs distinguish:

- no slots available
- calendar loaded but empty
- slot detected outside July 2026
- valid July 2026 slot detected

Each slot-state log includes a timestamp and a short visible German page-text snippet without applicant details.

## Heartbeat Email

Heartbeat emails are disabled by default. To enable a lightweight status email every four hours:

```yaml
heartbeat_enabled: true
heartbeat_interval_minutes: 240
```

The heartbeat includes monitoring-active status, last successful calendar check time, current retry/backoff interval, and dry-run status. The timer resets only after a successful heartbeat email.

## Duplicate Booking Protection

Real booking mode creates this local flag after a successful submission:

```text
confirmations/booking_success.flag
```

The flag is intentionally ignored by Git because it can contain appointment details. Leave it in place after a successful booking to prevent accidental duplicate submissions.

## Email Notification

After a real booking submission, the script sends an email with:

- appointment date
- appointment time
- location
- visible reference number, if available
- success screenshot, if safely available

Important: the official website may send a separate confirmation email with a link. The appointment may not be finalized until that official link is clicked.

## Gmail SMTP Test

Create a Gmail app password at <https://myaccount.google.com/apppasswords>, then set these `.env` values:

```dotenv
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USE_TLS=true
EMAIL_SENDER=your-gmail-address@gmail.com
EMAIL_APP_PASSWORD=your-16-character-app-password
EMAIL_RECEIVER=where-to-send-notifications@example.com
```

Send a test email:

```powershell
.\.venv\Scripts\Activate.ps1
python -m src.smtp_test
```

The command exits with a clear error if required SMTP values are missing or the Gmail app password is invalid.
If SMTP settings are empty, SMTP is treated as disabled and the test command exits successfully without sending email.

## Applicant Fields

The Step 5 form can only be inspected after a slot is selected. When the dry-run reaches Step 5, the script logs detected visible form labels to `logs/monitor.log` as `step5_fields_detected` and maps known labels to these `.env` variables:

- `APPLICANT_FIRST_NAME`
- `APPLICANT_LAST_NAME`
- `APPLICANT_EMAIL`
- `APPLICANT_PHONE`
- `APPLICANT_DATE_OF_BIRTH`
- `APPLICANT_NATIONALITY`
- `APPLICANT_PASSPORT_NUMBER`
- `APPLICANT_GENDER` (`male` maps to common German options such as `männlich`, `maennlich`, `m`, or `Herr`)

Before filling anything, the script validates all detected required fields. It stops with a clear error if a required mapped `.env` value is empty or if the website exposes a required field that is not mapped yet.

## Dry-Run Testing Process

1. Keep `dry_run: true` and `headless: false` in `config.yaml`.
2. Fill `.env` with applicant details and Gmail SMTP settings.
3. Run `python -m src.smtp_test` and confirm the test email arrives.
4. Run `python -m src.main`.
5. Confirm the visible browser reaches the calendar page and logs one of the slot states in `logs/monitor.log`.
6. If a July 2026 slot appears, dry-run mode selects it, fills Step 5, advances to the final overview page, and stops before final submission.
7. Review `step5_fields_detected` in `logs/monitor.log` if the site reports missing or unmapped applicant fields.

Only after dry-run reaches the final overview page safely, switch to:

```yaml
dry_run: false
```

Then run `python -m src.main` to start real monitoring.
