# Automatic Visa Appointment Booking

Python + Playwright automation for monitoring the StädteRegion Aachen appointment website and booking a configured appointment when a matching slot appears.

The project is written for this appointment flow:

- Function unit: `Ausländer- und Staatsangehörigkeitsbehörde`
- Location: `RWTH - Außenstelle Super C`
- Category: `RWTH Studenten`
- Website: <https://termine.staedteregion-aachen.de/auslaenderamt/>

The checked-in config starts in visible browser mode with `dry_run: true`. Dry-run mode navigates the booking flow and stops before final submission, which is the safe default for new users. Switch to `dry_run: false` only after you have verified the flow with your own applicant details.

This repository is meant to be adapted by the person running it. Change the appointment URL, category, location, target months, and applicant fields before relying on it. The project is not affiliated with StädteRegion Aachen and does not bypass CAPTCHA, queues, logins, rate limits, or anti-bot systems.

## Safety

- No CAPTCHA bypassing, queue bypassing, login bypassing, rate-limit evasion, or anti-bot circumvention.
- Monitoring uses `check_interval_seconds` from `config.yaml`.
- The script stops at `stop_at_time` from `config.yaml`, regardless of when it was started.
- The script keeps monitoring remaining profiles after one profile is booked, and stops once all configured profiles have success flags.
- After a successful real booking submission, the script creates a profile-specific success flag under `confirmations/`.
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

Edit `.env` with your applicant details and optional Gmail SMTP values. For Gmail, use an app password, not your normal account password.
Leave the SMTP settings empty to disable project email notifications; the official appointment email will still go to `APPLICANT_EMAIL`.

## Always-On VPS Setup

Use a small Ubuntu VPS so monitoring continues when your laptop is off. Create `.env` directly on the VPS; do not commit personal details, tokens, logs, screenshots, or confirmation files.

On the VPS:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv
sudo useradd --system --create-home --shell /usr/sbin/nologin visa-monitor
sudo mkdir -p /opt/visa-monitor
sudo chown visa-monitor:visa-monitor /opt/visa-monitor
```

Clone your fork or copy of the repository, then install dependencies:

```bash
sudo -u visa-monitor git clone <your-repository-url> /opt/visa-monitor/AutomaticVisaAppointmentBooking
cd /opt/visa-monitor/AutomaticVisaAppointmentBooking
sudo -u visa-monitor python3 -m venv .venv
sudo -u visa-monitor .venv/bin/python -m pip install -r requirements.txt
sudo -u visa-monitor .venv/bin/python -m playwright install chromium
sudo .venv/bin/python -m playwright install-deps chromium
```

Create the private `.env` file on the VPS:

```bash
sudo -u visa-monitor nano /opt/visa-monitor/AutomaticVisaAppointmentBooking/.env
```

For VPS monitoring, edit `config.yaml` after one local validation run:

```yaml
headless: true
dry_run: true
```

Run one dry-run check manually:

```bash
cd /opt/visa-monitor/AutomaticVisaAppointmentBooking
sudo -u visa-monitor .venv/bin/python -m src.main
```

After dry-run validation, set:

```yaml
dry_run: false
```

Install the systemd service:

```bash
sudo cp /opt/visa-monitor/AutomaticVisaAppointmentBooking/deploy/visa-monitor.service.example /etc/systemd/system/visa-monitor.service
sudo systemctl daemon-reload
sudo systemctl enable visa-monitor
sudo systemctl start visa-monitor
```

Check status and logs:

```bash
sudo systemctl status visa-monitor
sudo journalctl -u visa-monitor -f
sudo -u visa-monitor tail -f /opt/visa-monitor/AutomaticVisaAppointmentBooking/logs/monitor.log
```

Stop monitoring:

```bash
sudo systemctl stop visa-monitor
```

## Configuration

Edit `config.yaml`:

```yaml
website_url: "https://termine.staedteregion-aachen.de/auslaenderamt/"
visa_category: "RWTH Studenten"
appointment_location: "RWTH - Außenstelle Super C"
applicant_profiles:
  - name: "applicant"
    env_prefix: "APPLICANT"
    target_months:
      - "2026-09"
      - "2026-10"
check_interval_seconds: 10
stop_at_time: "17:00"
headless: false
dry_run: true
browser_timeout_seconds: 30
heartbeat_enabled: false
heartbeat_interval_minutes: 240
```

Keep `headless: false` while validating the flow, especially if a manual captcha/security challenge may appear. For validation, temporarily set:

```yaml
dry_run: true
```

After dry-run validation, set:

```yaml
dry_run: false
```

Only use real booking mode after confirming the dry-run reaches the final overview page correctly.

With `applicant_profiles`, each detected slot is routed by month. In the example above, September/October slots fill from `APPLICANT_*` values. Success flags are profile-specific, so a previous booking for another profile does not block the active profile.

## Run

```powershell
.\.venv\Scripts\Activate.ps1
python -m src.main
```

Stop monitoring with `Ctrl+C`. The browser is closed by Playwright cleanup and a final monitoring summary is written to `logs/monitor.log`.
The monitor also stops automatically at the configured daily `stop_at_time`, which is `17:00` by default in this checkout.

## Start From Phone With Tailscale

The phone can start or stop the bot while the actual browser and monitoring process run on the laptop. The normal laptop command stays unchanged:

```powershell
python -m src.main
```

For phone control, use Tailscale so the control page is reachable privately from your phone without exposing the laptop to the public internet.

One-time setup:

1. Install Tailscale on the laptop and phone: <https://tailscale.com/download>
2. Sign into the same Tailscale account on both devices.
3. On the laptop, install the phone-control startup task:

```powershell
.\.venv\Scripts\Activate.ps1
.\deploy\install-phone-control-task.ps1
```

That script:

- creates `CONTROL_TOKEN` in `.env` if it is missing
- registers a Windows scheduled task named `VisaBotControlServer`
- starts the control page now
- starts the control page automatically each time you log into Windows

After setup, you do not need to type a laptop command when using your phone. Keep the laptop powered on and logged into Windows. On your phone, open Tailscale, copy the laptop's `100.x.y.z` address, then open:

```text
http://100.x.y.z:8765
```

Enter the `CONTROL_TOKEN` from `.env`, then tap **Start Bot**. The bot runs on the laptop. The phone page shows running/stopped status, uptime, stop control, and logs from the latest phone-started run. The logs page also has a **Show all logs** link for older history.

To include this phone-control page in Telegram alerts, add it to `.env`:

```dotenv
CONTROL_PAGE_URL=http://100.x.y.z:8765
```

If an appointment is found and the website asks for the manual security answer, booking remains on the laptop but the phone page shows an **Action Required** panel. It includes a cropped screenshot of the security challenge and an answer box. Submit the answer from the phone and the laptop bot fills it into the browser and continues. Keep the phone control page open if you want the browser sound/vibration alert when the challenge appears; closed mobile browsers cannot receive local web-page push alerts. Some mobile browsers require you to interact with the page once before they allow sound.

If the manual challenge is an **I am not a robot** CAPTCHA, the bot does not solve or click it. The phone page shows a **Manual Browser Action** panel with a screenshot and sound/vibration alerts, then the laptop bot pauses. Complete the CAPTCHA manually in the laptop browser, for example through Chrome Remote Desktop, AnyDesk, Windows Remote Desktop over Tailscale, or by being at the laptop. After you finish the CAPTCHA, press Enter in the browser to let the bot continue.

For outside-home CAPTCHA handling, the recommended setup is Chrome Remote Desktop:

1. Set up remote access on the laptop at <https://remotedesktop.google.com/access>.
2. Install the Chrome Remote Desktop app on your phone.
3. Confirm you can open and control the laptop from your phone before relying on the bot.
4. When Telegram or the phone page says **Manual CAPTCHA required**, open Chrome Remote Desktop on the phone.
5. Tap the CAPTCHA in the laptop browser remotely.
6. Press Enter in the laptop browser after the challenge is complete, then return to the phone control page or Telegram.

If the laptop bot encounters an error while running, the same phone page shows a **Bot Alert** panel with the error summary, retry details when the bot is recovering, and an error screenshot link when available. The full details are still written on the laptop in `logs/monitor.log` and `logs/bot_process.log`.

## Telegram Alerts

Telegram alerts are optional and are useful because phone browser alerts may pause when the screen is locked. Telegram bots cannot send a message to a phone number directly; each recipient must first open the bot in Telegram and send `/start`.

1. In Telegram, open `@BotFather`.
2. Create a bot with `/newbot`.
3. Copy the bot token into `.env`:

```dotenv
TELEGRAM_BOT_TOKEN=your-bot-token
```

4. Open your new bot in Telegram and send it `/start`. The bot saves that chat as a future alert recipient and replies with a subscription confirmation. For multiple recipients, each person sends `/start` to the same bot. Anyone can send `/stop` later to unsubscribe.
5. Optional: on the laptop, run this command to immediately import pending `/start` messages and show the current subscriber list:

```powershell
.\.venv\Scripts\Activate.ps1
python -m src.telegram_setup
```

Subscribers are stored locally in `logs/telegram_subscribers.json`, which is ignored by Git. The phone control server and the monitor refresh Telegram subscriptions about once per minute while they are running. Every alert also refreshes subscriptions before sending, so a new `/start` is picked up before that alert goes out.

Static chat IDs are still supported but are optional. Use them only if you want fixed recipients in `.env`:

```dotenv
TELEGRAM_CHAT_ID=123456789,987654321
TELEGRAM_ALERT_THROTTLE_SECONDS=300
```

When configured, the bot sends Telegram alerts for:

- valid target-month appointment slots, before booking is attempted, with slot details, a screenshot, the fresh website start URL, and `CONTROL_PAGE_URL` when configured
- manual text security/captcha answers
- manual CAPTCHA / **I am not a robot** challenges
- fatal errors that stop the bot
- recoverable errors where the bot is retrying

Screenshots are attached when available. Slot-found alerts bypass throttling so the first detection is sent immediately; repeated identical recoverable errors are throttled by `TELEGRAM_ALERT_THROTTLE_SECONDS`.

The appointment site's Step 4 calendar URL is session-specific. Opening a Step 4 URL such as `/suggest` from Telegram or another browser can show `No valid location found`. Use the already-open laptop browser through remote desktop if you need to manually click the date, or let the bot continue in that active session.

To run the control page manually instead of using the scheduled task:

```powershell
.\.venv\Scripts\Activate.ps1
python -m src.control_server
```

To remove the phone-control startup task:

```powershell
.\deploy\uninstall-phone-control-task.ps1
```

If your phone cannot open the Tailscale URL, confirm both devices are online in Tailscale and allow Python or TCP port `8765` through Windows Firewall.

If you only want same-Wi-Fi access without Tailscale, open the laptop's local IP with port `8765` instead, for example `http://192.168.1.25:8765`.

## Booking Flow

The script performs this German website flow:

1. Opens the appointment website.
2. Clicks `Ausländer- und Staatsangehörigkeitsbehörde`.
3. Opens `RWTH - Außenstelle Super C`.
4. Increments `RWTH Studenten` to one applicant.
5. Clicks `Weiter`.
6. Accepts the information dialog with `OK` if it is shown.
7. Clicks `Weiter` on the location/address page.
8. Monitors the appointment calendar page for the configured profile target months.
9. Selects the first visible slot in an active profile target month.
10. Confirms the selected appointment summary by clicking `Ja` if that confirmation page is shown.
11. Fills personal details from the `.env` prefix for the applicant profile matched to the slot month.
12. In dry-run mode, stops before final submission.
13. In real mode, clicks `Book now` / `Jetzt buchen`, saves confirmation evidence, sends a Gmail SMTP notification if SMTP is configured, and stops.

If the session expires or the navigation no longer matches the expected flow, the script logs the error, saves an error screenshot, and restarts from Step 1.
Repeated navigation failures use temporary exponential backoff before restarting the flow. The backoff resets after the calendar page loads successfully.

During monitoring, slot-state logs distinguish:

- no slots available
- calendar loaded but empty
- slot detected outside the currently active profile target months
- valid profile target-month slot detected

Each slot-state log includes a timestamp and a short visible German page-text snippet without applicant details.

The monitor also records month-level availability while it navigates through the configured profile target months. Booking remains restricted to months for profiles that do not already have a success flag, but the log now includes:

- `month_slot_state` for each month page observed during the check
- `non_target_slot_month_seen` the first time an opening is seen outside the active profile target months
- `open_months_seen` in the final `monitor_summary`

Use these entries to see which months had openings during the run without allowing the script to book outside the configured profile-month routing.

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

After a real booking submission, the script saves a success screenshot and confirmation text locally. If SMTP is configured, it also sends an email with:

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

The Step 5 form can only be inspected after a slot is selected. When the script reaches Step 5, it logs detected visible form labels to `logs/monitor.log` as `step5_fields_detected` and maps known labels to these base `.env` variables:

- `APPLICANT_FIRST_NAME`
- `APPLICANT_LAST_NAME`
- `APPLICANT_EMAIL`
- `APPLICANT_PHONE`
- `APPLICANT_DATE_OF_BIRTH`
- `APPLICANT_REMARKS`
- `APPLICANT_SECURITY_ANSWER`
- `APPLICANT_DATA_PROCESSING_CONSENT` (`true`, `yes`, or `ja` checks the required GDPR consent box)
- `APPLICANT_SAVE_PERSONAL_DATA_LOCALLY` (`true`, `yes`, or `ja` checks the optional local-save box)
- `APPLICANT_NATIONALITY`
- `APPLICANT_PASSPORT_NUMBER`
- `APPLICANT_GENDER` (`male` maps to common German options such as `männlich`, `maennlich`, `m`, or `Herr`)

For the active profile configured with `env_prefix: "APPLICANT"`, use the same field suffixes with that prefix, for example `APPLICANT_FIRST_NAME`, `APPLICANT_LAST_NAME`, `APPLICANT_EMAIL`, and `APPLICANT_DATE_OF_BIRTH`.

For the active profile, `APPLICANT_EMAIL` is used for both the email and repeated-email fields. `APPLICANT_DATE_OF_BIRTH` can be entered as `DD-MM-YYYY`, `DD.MM.YYYY`, `DD/MM/YYYY`, or `YYYY-MM-DD`; the script formats it for one combined date field or splits it into day, month, and year fields when the website shows separate inputs.

Leave `APPLICANT_SECURITY_ANSWER` empty when a visible text security challenge must be entered manually. When the script reaches that field, it restores the visible browser window if it was minimized, brings the tab to the front, focuses the field, plays an alert sound immediately, and repeats the sound every 10 seconds until an answer is entered. If the bot was started from the phone control page, the same manual text challenge can also be answered from the phone.

For checkbox-style CAPTCHA challenges such as **I am not a robot**, the phone page can alert you and show a screenshot, but you must complete the CAPTCHA manually in the laptop browser and press Enter afterward so the bot knows it can continue. This project does not bypass CAPTCHA or automate anti-bot checks.

Field detection uses German synonym groups, so labels such as `Geburtsdatum`, `Datum der Geburt`, or split `Tag` / `Monat` / `Jahr` date fields map to the same birth-date value. The same approach is used for names, email, telephone, remarks, security question, consent, nationality, passport number, and gender.

Before filling anything, the script validates all detected required fields. It stops with a clear error if a required mapped `.env` value is empty or if the website exposes a required field that is not mapped yet.

## Dry-Run Testing Process

1. Set `dry_run: true` and `headless: false` in `config.yaml`.
2. Fill `.env` with applicant details and Gmail SMTP settings.
3. Run `python -m src.smtp_test` and confirm the test email arrives.
4. Run `python -m src.main`.
5. Confirm the visible browser reaches the calendar page and logs one of the slot states in `logs/monitor.log`.
6. If a configured target-month slot appears, dry-run mode selects it, fills Step 5 with the applicant profile for that month, advances to the final overview page, and stops before final submission.
7. Review `step5_fields_detected` in `logs/monitor.log` if the site reports missing or unmapped applicant fields.

Only after dry-run reaches the final overview page safely, switch to:

```yaml
dry_run: false
```

Then run `python -m src.main` to start real monitoring.
