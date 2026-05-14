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
- `.env`, logs, screenshots, traces, videos, and confirmation files are ignored by Git.
- Screenshots are saved only on errors or successful real booking.

## Setup On Windows

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
Copy-Item .env.example .env
```

Edit `.env` with applicant details and Gmail SMTP values. For Gmail, use an app password, not your normal account password.

## Configuration

Edit `config.yaml`:

```yaml
check_interval_seconds: 300
max_runtime_minutes: 720
headless: false
dry_run: true
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

## Email Notification

After a real booking submission, the script sends an email with:

- appointment date
- appointment time
- location
- visible reference number, if available
- success screenshot, if safely available

Important: the official website may send a separate confirmation email with a link. The appointment may not be finalized until that official link is clicked.

## Applicant Fields

The current `.env.example` includes common applicant variables:

- `APPLICANT_FIRST_NAME`
- `APPLICANT_LAST_NAME`
- `APPLICANT_EMAIL`
- `APPLICANT_PHONE`
- `APPLICANT_DATE_OF_BIRTH`
- `APPLICANT_NATIONALITY`
- `APPLICANT_PASSPORT_NUMBER`

If the live form uses additional required fields, dry-run mode will log the missing labels so `.env.example` and `src/form_filler.py` can be extended safely.
