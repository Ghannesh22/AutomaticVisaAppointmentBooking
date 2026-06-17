from __future__ import annotations

import argparse
import html
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from dotenv import load_dotenv

from src.config_loader import PROJECT_ROOT
from src.manual_action_state import clear_manual_action_request, load_manual_action_request
from src.phone_alerts import clear_error_alert, load_error_alert
from src.security_state import load_security_request, submit_security_answer
from src.telegram_notifier import refresh_telegram_subscribers


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8765
COOKIE_NAME = "visa_control_token"
STARTUP_SETTLE_SECONDS = 1.5
TELEGRAM_POLL_SECONDS = 60


class BotProcess:
    def __init__(self, command: list[str], cwd: Path) -> None:
        self.command = command
        self.cwd = cwd
        self.process: subprocess.Popen[str] | None = None
        self.log_handle: Any | None = None
        self.started_at: float | None = None
        self.monitor_log_start_offset = _file_size(cwd / "logs" / "monitor.log")
        self.bot_log_start_offset = _file_size(cwd / "logs" / "bot_process.log")

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def status(self) -> str:
        if self.is_running():
            return "running"
        if self.process is None:
            return "stopped"
        return f"exited ({self.process.returncode})"

    def uptime(self) -> str:
        if not self.is_running() or self.started_at is None:
            return "-"
        seconds = int(time.time() - self.started_at)
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def start(self) -> str:
        if self.is_running():
            return "Bot is already running."

        clear_error_alert()
        clear_manual_action_request()
        logs_dir = self.cwd / "logs"
        logs_dir.mkdir(exist_ok=True)
        self._close_log_handle()
        monitor_log = logs_dir / "monitor.log"
        bot_log = logs_dir / "bot_process.log"
        self.monitor_log_start_offset = _file_size(monitor_log)
        self.bot_log_start_offset = _file_size(bot_log)
        self.log_handle = bot_log.open("a", encoding="utf-8")
        self.started_at = time.time()
        self.log_handle.write(f"\n--- bot started from control server at {_format_time(self.started_at)} ---\n")
        self.log_handle.flush()

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

        self.process = subprocess.Popen(
            self.command,
            cwd=self.cwd,
            env=env,
            stdout=self.log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=creationflags,
        )
        time.sleep(STARTUP_SETTLE_SECONDS)
        returncode = self.process.poll()
        if returncode is not None:
            self._close_log_handle()
            return f"Bot exited immediately (exit code {returncode}). Open recent logs for details."
        return "Bot started on the laptop."

    def stop(self) -> str:
        if not self.is_running() or self.process is None:
            self._close_log_handle()
            return "Bot is not running."

        self.process.terminate()
        try:
            self.process.wait(timeout=10)
            message = "Bot stopped."
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
            message = "Bot did not stop cleanly, so it was killed."
        self._close_log_handle()
        return message

    def _close_log_handle(self) -> None:
        if self.log_handle is not None:
            self.log_handle.close()
            self.log_handle = None


class ControlState:
    def __init__(self, token: str, bot: BotProcess) -> None:
        self.token = token
        self.bot = bot


class ControlRequestHandler(BaseHTTPRequestHandler):
    server_version = "VisaControl/1.0"

    @property
    def state(self) -> ControlState:
        return self.server.state  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            if not self._authenticated():
                self._render_login()
                return
            self._render_dashboard()
            return
        if path == "/logs":
            if not self._authenticated():
                self._redirect("/")
                return
            self._render_logs()
            return
        if path == "/status":
            if not self._authenticated():
                self._send_text("unauthorized", HTTPStatus.UNAUTHORIZED)
                return
            self._send_text(self.state.bot.status())
            return
        if path == "/logout":
            self._clear_cookie()
            return
        if path == "/security-image":
            if not self._authenticated():
                self._send_text("unauthorized", HTTPStatus.UNAUTHORIZED)
                return
            self._send_security_image()
            return
        if path == "/error-image":
            if not self._authenticated():
                self._send_text("unauthorized", HTTPStatus.UNAUTHORIZED)
                return
            self._send_error_image()
            return
        if path == "/manual-action-image":
            if not self._authenticated():
                self._send_text("unauthorized", HTTPStatus.UNAUTHORIZED)
                return
            self._send_manual_action_image()
            return
        self._send_text("not found", HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        form = self._read_form()
        if path == "/login":
            token = form.get("token", [""])[0]
            if secrets.compare_digest(token, self.state.token):
                self._set_cookie(token)
                self._redirect("/")
                return
            self._render_login("Wrong token.")
            return

        if not self._authenticated(form):
            self._redirect("/")
            return

        if path == "/start":
            message = self.state.bot.start()
            self._redirect(f"/?{urlencode({'message': message})}")
            return
        if path == "/stop":
            message = self.state.bot.stop()
            self._redirect(f"/?{urlencode({'message': message})}")
            return
        if path == "/security-answer":
            message = self._submit_security_answer(form)
            self._redirect(f"/?{urlencode({'message': message})}")
            return
        if path == "/dismiss-error":
            clear_error_alert()
            self._redirect(f"/?{urlencode({'message': 'Error alert dismissed.'})}")
            return
        self._send_text("not found", HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write("%s | %s\n" % (self.log_date_time_string(), format % args))

    def _authenticated(self, form: dict[str, list[str]] | None = None) -> bool:
        token = ""
        cookie_header = self.headers.get("Cookie")
        if cookie_header:
            cookies = SimpleCookie(cookie_header)
            if COOKIE_NAME in cookies:
                token = cookies[COOKIE_NAME].value
        if not token:
            query = parse_qs(urlparse(self.path).query)
            token = query.get("token", [""])[0]
        if not token and form:
            token = form.get("token", [""])[0]
        return secrets.compare_digest(token, self.state.token)

    def _read_form(self) -> dict[str, list[str]]:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8") if length else ""
        return parse_qs(body)

    def _render_login(self, message: str = "") -> None:
        content = _page(
            "Visa Bot Control",
            f"""
            <h1>Visa Bot Control</h1>
            <p>Enter the control token printed in the laptop terminal.</p>
            {_message_html(message)}
            <form method="post" action="/login">
              <label>Control token</label>
              <input name="token" type="password" autocomplete="current-password" autofocus>
              <button type="submit">Unlock</button>
            </form>
            """,
        )
        self._send_html(content)

    def _render_dashboard(self) -> None:
        query = parse_qs(urlparse(self.path).query)
        message = query.get("message", [""])[0]
        status = self.state.bot.status()
        running = self.state.bot.is_running()
        status_class = "running" if running else "stopped"
        security_panel = _security_panel_html()
        error_panel = _error_panel_html()
        manual_action_panel = _manual_action_panel_html()
        content = _page(
            "Visa Bot Control",
            f"""
            <h1>Visa Bot Control</h1>
            {_message_html(message)}
            {error_panel}
            {manual_action_panel}
            {security_panel}
            <div class="panel">
              <div class="label">Status</div>
              <div class="status {status_class}">{html.escape(status)}</div>
              <div class="meta">Uptime: {html.escape(self.state.bot.uptime())}</div>
            </div>
            <form method="post" action="/start">
              <button type="submit" {"disabled" if running else ""}>Start Bot</button>
            </form>
            <form method="post" action="/stop">
              <button class="danger" type="submit" {"disabled" if not running else ""}>Stop Bot</button>
            </form>
            <p><a href="/logs">View recent logs</a> - <a href="/logout">Forget this device</a></p>
            """,
            refresh_seconds=5,
        )
        self._send_html(content)

    def _submit_security_answer(self, form: dict[str, list[str]]) -> str:
        challenge_id = form.get("challenge_id", [""])[0]
        answer = form.get("answer", [""])[0].strip()
        pending = load_security_request()
        if not pending or pending.get("id") != challenge_id:
            return "No active security challenge is waiting for an answer."
        if not answer:
            return "Security answer was empty."
        submit_security_answer(challenge_id, answer)
        return "Security answer sent to the laptop bot."

    def _render_logs(self) -> None:
        query = parse_qs(urlparse(self.path).query)
        show_all = query.get("all", [""])[0] == "1"
        log_dir = PROJECT_ROOT / "logs"
        if show_all:
            log_text = _tail_text(log_dir / "monitor.log", max_bytes=24000)
            bot_log_text = _tail_text(log_dir / "bot_process.log", max_bytes=12000)
            scope_label = "Showing previous logs too."
            toggle_link = '<a href="/logs">Show only this phone-started run</a>'
        else:
            log_text = _read_from_offset(log_dir / "monitor.log", self.state.bot.monitor_log_start_offset)
            bot_log_text = _read_from_offset(log_dir / "bot_process.log", self.state.bot.bot_log_start_offset)
            scope_label = "Showing logs from the latest phone-started bot run."
            toggle_link = '<a href="/logs?all=1">Show all logs</a>'
        started = _format_time(self.state.bot.started_at) if self.state.bot.started_at else "not started from phone yet"
        content = _page(
            "Visa Bot Logs",
            f"""
            <h1>Recent Logs</h1>
            <p><a href="/">Back to control</a> - {toggle_link}</p>
            <div class="panel">
              <div class="label">Log scope</div>
              <div>{html.escape(scope_label)}</div>
              <div class="meta">Run started: {html.escape(started)}</div>
            </div>
            <h2>monitor.log</h2>
            <pre>{html.escape(log_text or "No monitor log for this run yet.")}</pre>
            <h2>bot_process.log</h2>
            <pre>{html.escape(bot_log_text or "No bot process log for this run yet.")}</pre>
            """,
            refresh_seconds=10,
        )
        self._send_html(content)

    def _redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.end_headers()

    def _set_cookie(self, token: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", "/")
        self.send_header(
            "Set-Cookie",
            f"{COOKIE_NAME}={token}; Max-Age=31536000; HttpOnly; SameSite=Lax; Path=/",
        )
        self.end_headers()

    def _clear_cookie(self) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", "/")
        self.send_header(
            "Set-Cookie",
            f"{COOKIE_NAME}=; Max-Age=0; HttpOnly; SameSite=Lax; Path=/",
        )
        self.end_headers()

    def _send_html(self, content: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_text(self, content: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_security_image(self) -> None:
        pending = load_security_request()
        if not pending:
            self._send_text("not found", HTTPStatus.NOT_FOUND)
            return
        image_path_text = str(pending.get("image_path", ""))
        image_path = Path(image_path_text) if image_path_text else None
        if not image_path or not image_path.exists() or not image_path.is_file():
            self._send_text("not found", HTTPStatus.NOT_FOUND)
            return
        content = image_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/png")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _send_error_image(self) -> None:
        alert = load_error_alert()
        if not alert:
            self._send_text("not found", HTTPStatus.NOT_FOUND)
            return
        image_path_text = str(alert.get("screenshot_path", ""))
        image_path = Path(image_path_text) if image_path_text else None
        if not image_path or not image_path.exists() or not image_path.is_file():
            self._send_text("not found", HTTPStatus.NOT_FOUND)
            return
        content = image_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/png")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _send_manual_action_image(self) -> None:
        request = load_manual_action_request()
        if not request:
            self._send_text("not found", HTTPStatus.NOT_FOUND)
            return
        image_path_text = str(request.get("image_path", ""))
        image_path = Path(image_path_text) if image_path_text else None
        if not image_path or not image_path.exists() or not image_path.is_file():
            self._send_text("not found", HTTPStatus.NOT_FOUND)
            return
        content = image_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/png")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def _page(title: str, body: str, refresh_seconds: int | None = None) -> str:
    refresh = f'<meta http-equiv="refresh" content="{refresh_seconds}">' if refresh_seconds else ""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  {refresh}
  <title>{html.escape(title)}</title>
  <style>
    body {{
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      margin: 0;
      background: #f6f7f8;
      color: #111827;
    }}
    main {{
      width: min(680px, calc(100% - 32px));
      margin: 32px auto;
    }}
    h1 {{ font-size: 28px; margin: 0 0 20px; }}
    h2 {{ font-size: 18px; margin: 28px 0 10px; }}
    .panel {{
      background: #ffffff;
      border: 1px solid #d7dce2;
      border-radius: 8px;
      padding: 18px;
      margin-bottom: 16px;
    }}
    .label {{ color: #526070; font-size: 14px; }}
    .status {{ font-size: 26px; font-weight: 700; margin-top: 4px; }}
    .running {{ color: #067647; }}
    .stopped {{ color: #9b1c1c; }}
    .meta {{ color: #526070; margin-top: 6px; }}
    form {{ margin: 12px 0; }}
    label {{ display: block; font-weight: 600; margin-bottom: 8px; }}
    input {{
      box-sizing: border-box;
      width: 100%;
      padding: 12px;
      border: 1px solid #b7c0ca;
      border-radius: 8px;
      font: inherit;
      margin-bottom: 12px;
    }}
    button {{
      width: 100%;
      padding: 13px 16px;
      border: 0;
      border-radius: 8px;
      background: #174ea6;
      color: white;
      font: inherit;
      font-weight: 700;
    }}
    button.danger {{ background: #b42318; }}
    button:disabled {{ background: #9aa4b2; }}
    .message {{
      background: #e8f1ff;
      border: 1px solid #b7d2ff;
      border-radius: 8px;
      padding: 12px;
      margin-bottom: 16px;
    }}
    .action-required {{
      border-color: #f5a524;
      background: #fff8eb;
    }}
    .manual-action {{
      border-color: #d97706;
      background: #fff7ed;
    }}
    .error-alert {{
      border-color: #f04438;
      background: #fff1f0;
    }}
    .action-required h2 {{
      margin-top: 6px;
    }}
    .manual-action h2 {{
      margin-top: 6px;
    }}
    .error-alert h2 {{
      margin-top: 6px;
    }}
    .security-image,
    .manual-action-image,
    .error-image {{
      box-sizing: border-box;
      width: 100%;
      max-height: 420px;
      object-fit: contain;
      background: white;
      border: 1px solid #d7dce2;
      border-radius: 8px;
      margin: 14px 0;
    }}
    pre {{
      overflow: auto;
      white-space: pre-wrap;
      background: #111827;
      color: #f9fafb;
      border-radius: 8px;
      padding: 14px;
      font-size: 12px;
      line-height: 1.45;
    }}
    a {{ color: #174ea6; font-weight: 600; }}
  </style>
</head>
<body>
  <main>{body}</main>
</body>
</html>"""


def _message_html(message: str) -> str:
    if not message:
        return ""
    return f'<div class="message">{html.escape(message)}</div>'


def _error_panel_html() -> str:
    alert = load_error_alert()
    if not alert:
        return ""
    alert_id = str(alert.get("id", ""))
    title = str(alert.get("title", "Bot error"))
    message = str(alert.get("message", "Unknown error"))
    severity = str(alert.get("severity", "error"))
    created_at = str(alert.get("created_at", ""))
    retry_count = alert.get("retry_count")
    next_wait_seconds = alert.get("next_wait_seconds")
    screenshot_mtime = str(alert.get("screenshot_mtime", "0"))
    image_path_text = str(alert.get("screenshot_path", ""))
    image_path = Path(image_path_text) if image_path_text else None
    image_html = ""
    if image_path and image_path.exists() and image_path.is_file():
        image_html = (
            f'<p><a href="/error-image?v={html.escape(screenshot_mtime)}" target="_blank">'
            "Open error screenshot</a></p>"
            f'<img class="error-image" src="/error-image?v={html.escape(screenshot_mtime)}" '
            'alt="Latest error screenshot">'
        )
    details = []
    if retry_count is not None:
        details.append(f"Retry count: {retry_count}")
    if next_wait_seconds is not None:
        details.append(f"Next retry wait: {next_wait_seconds} seconds")
    details_html = "".join(f'<div class="meta">{html.escape(str(detail))}</div>' for detail in details)
    return f"""
    <div class="panel error-alert">
      <div class="label">Bot Alert</div>
      <h2>{html.escape(title)}</h2>
      <div class="meta">Severity: {html.escape(severity)}</div>
      <div class="meta">Detected: {html.escape(created_at)}</div>
      {details_html}
      <p>{html.escape(message)}</p>
      {image_html}
      <p><a href="/logs">View latest run logs</a></p>
      <form method="post" action="/dismiss-error">
        <button class="danger" type="submit">Dismiss Error Alert</button>
      </form>
      <script>
        (function () {{
          var alertId = "{html.escape(alert_id)}";
          var alertKey = "bot-error-alert-" + alertId;
          function vibrate() {{
            if (navigator.vibrate) navigator.vibrate([700, 200, 700, 200, 1000]);
          }}
          function beep() {{
            try {{
              var AudioContext = window.AudioContext || window.webkitAudioContext;
              if (!AudioContext) return false;
              var context = new AudioContext();
              var now = context.currentTime;
              [0, 0.25, 0.5, 0.9].forEach(function (offset) {{
                var oscillator = context.createOscillator();
                var gain = context.createGain();
                oscillator.type = "square";
                oscillator.frequency.value = 660;
                gain.gain.setValueAtTime(0.0001, now + offset);
                gain.gain.exponentialRampToValueAtTime(0.3, now + offset + 0.03);
                gain.gain.exponentialRampToValueAtTime(0.0001, now + offset + 0.16);
                oscillator.connect(gain);
                gain.connect(context.destination);
                oscillator.start(now + offset);
                oscillator.stop(now + offset + 0.18);
              }});
              setTimeout(function () {{ context.close(); }}, 1500);
              return true;
            }} catch (error) {{
              return false;
            }}
          }}
          if (!window.sessionStorage || sessionStorage.getItem(alertKey) !== "1") {{
            if (window.sessionStorage) sessionStorage.setItem(alertKey, "1");
            vibrate();
            if (!beep()) {{
              setTimeout(function () {{ alert("The laptop bot reported an error."); }}, 200);
            }}
          }}
        }})();
      </script>
    </div>
    """


def _manual_action_panel_html() -> str:
    request = load_manual_action_request()
    if not request:
        return ""
    request_id = str(request.get("id", ""))
    title = str(request.get("title", "Manual action required"))
    message = str(request.get("message", "Complete the manual action in the laptop browser."))
    created_at = str(request.get("created_at", ""))
    image_mtime = str(request.get("image_mtime", "0"))
    image_path_text = str(request.get("image_path", ""))
    image_path = Path(image_path_text) if image_path_text else None
    image_html = ""
    if image_path and image_path.exists() and image_path.is_file():
        image_html = (
            f'<p><a href="/manual-action-image?v={html.escape(image_mtime)}" target="_blank">'
            "Open latest laptop screenshot</a></p>"
            f'<img class="manual-action-image" src="/manual-action-image?v={html.escape(image_mtime)}" '
            'alt="Manual action screenshot">'
        )
    return f"""
    <div class="panel manual-action">
      <div class="label">Manual Browser Action</div>
      <h2>{html.escape(title)}</h2>
      <div class="meta">Detected: {html.escape(created_at)}</div>
      <p>{html.escape(message)}</p>
      {image_html}
      <p>Open Chrome Remote Desktop or another remote desktop app on your phone, control the laptop browser, and complete this manually. The bot will continue automatically after the challenge clears.</p>
      <script>
        (function () {{
          var requestId = "{html.escape(request_id)}";
          var soundKey = "manual-action-sound-" + requestId;
          function vibrate() {{
            if (navigator.vibrate) navigator.vibrate([700, 200, 700, 200, 1200]);
          }}
          function beep() {{
            try {{
              var AudioContext = window.AudioContext || window.webkitAudioContext;
              if (!AudioContext) return false;
              var context = new AudioContext();
              var now = context.currentTime;
              [0, 0.3, 0.6, 1.0].forEach(function (offset) {{
                var oscillator = context.createOscillator();
                var gain = context.createGain();
                oscillator.type = "sine";
                oscillator.frequency.value = 990;
                gain.gain.setValueAtTime(0.0001, now + offset);
                gain.gain.exponentialRampToValueAtTime(0.35, now + offset + 0.03);
                gain.gain.exponentialRampToValueAtTime(0.0001, now + offset + 0.2);
                oscillator.connect(gain);
                gain.connect(context.destination);
                oscillator.start(now + offset);
                oscillator.stop(now + offset + 0.22);
              }});
              setTimeout(function () {{ context.close(); }}, 1600);
              return true;
            }} catch (error) {{
              return false;
            }}
          }}
          function alertNow() {{
            vibrate();
            if (!beep() && window.sessionStorage && sessionStorage.getItem(soundKey) !== "alerted") {{
              sessionStorage.setItem(soundKey, "alerted");
              setTimeout(function () {{ alert("Manual CAPTCHA action needed on the laptop browser."); }}, 200);
            }}
          }}
          alertNow();
          if (!window.sessionStorage || sessionStorage.getItem(soundKey) !== "repeating") {{
            if (window.sessionStorage) sessionStorage.setItem(soundKey, "repeating");
            setInterval(alertNow, 10000);
          }}
        }})();
      </script>
    </div>
    """


def _security_panel_html() -> str:
    pending = load_security_request()
    if not pending:
        return ""
    challenge_id = str(pending.get("id", ""))
    label = str(pending.get("label", "Manual security challenge"))
    created_at = str(pending.get("created_at", ""))
    image_mtime = str(pending.get("image_mtime", "0"))
    image_html = ""
    image_path_text = str(pending.get("image_path", ""))
    image_path = Path(image_path_text) if image_path_text else None
    if image_path and image_path.exists() and image_path.is_file():
        image_html = (
            f'<img class="security-image" src="/security-image?v={html.escape(image_mtime)}" '
            'alt="Manual security challenge screenshot">'
        )
    return f"""
    <div class="panel action-required">
      <div class="label">Action Required</div>
      <h2>Security answer needed</h2>
      <p>The laptop bot found an appointment and is waiting for the manual security answer.</p>
      <div class="meta">Field: {html.escape(label)}</div>
      <div class="meta">Detected: {html.escape(created_at)}</div>
      {image_html}
      <form method="post" action="/security-answer">
        <input type="hidden" name="challenge_id" value="{html.escape(challenge_id)}">
        <label>Security answer</label>
        <input name="answer" autocomplete="one-time-code" autofocus>
        <button type="submit">Send Answer To Laptop</button>
      </form>
      <script>
        (function () {{
          var challengeId = "{html.escape(challenge_id)}";
          var alertedKey = "security-alert-" + challengeId;
          var soundKey = "security-sound-" + challengeId;
          function vibrate() {{
            if (navigator.vibrate) navigator.vibrate([500, 180, 500, 180, 900]);
          }}
          function beep() {{
            try {{
              var AudioContext = window.AudioContext || window.webkitAudioContext;
              if (!AudioContext) return false;
              var context = new AudioContext();
              var now = context.currentTime;
              [0, 0.28, 0.56].forEach(function (offset) {{
                var oscillator = context.createOscillator();
                var gain = context.createGain();
                oscillator.type = "sine";
                oscillator.frequency.value = 880;
                gain.gain.setValueAtTime(0.0001, now + offset);
                gain.gain.exponentialRampToValueAtTime(0.35, now + offset + 0.03);
                gain.gain.exponentialRampToValueAtTime(0.0001, now + offset + 0.18);
                oscillator.connect(gain);
                gain.connect(context.destination);
                oscillator.start(now + offset);
                oscillator.stop(now + offset + 0.2);
              }});
              setTimeout(function () {{ context.close(); }}, 1100);
              return true;
            }} catch (error) {{
              return false;
            }}
          }}
          function alertNow() {{
            vibrate();
            if (!beep() && window.sessionStorage && sessionStorage.getItem(alertedKey) !== "1") {{
              sessionStorage.setItem(alertedKey, "1");
              setTimeout(function () {{ alert("Security answer needed for the laptop bot."); }}, 200);
            }}
          }}
          alertNow();
          if (!window.sessionStorage || sessionStorage.getItem(soundKey) !== "1") {{
            if (window.sessionStorage) sessionStorage.setItem(soundKey, "1");
            setInterval(alertNow, 10000);
          }}
        }})();
      </script>
    </div>
    """


def _tail_text(path: Path, max_bytes: int) -> str:
    if not path.exists():
        return ""
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - max_bytes), os.SEEK_SET)
        return handle.read().decode("utf-8", errors="replace")


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


def _read_from_offset(path: Path, offset: int, max_bytes: int = 36000) -> str:
    if not path.exists():
        return ""
    size = path.stat().st_size
    if offset > size:
        offset = 0
    if size - offset > max_bytes:
        offset = size - max_bytes
    with path.open("rb") as handle:
        handle.seek(offset)
        return handle.read().decode("utf-8", errors="replace")


def _format_time(timestamp: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))


def _local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def _control_token() -> tuple[str, bool]:
    load_dotenv(PROJECT_ROOT / ".env")
    token = os.getenv("CONTROL_TOKEN", "").strip()
    if token:
        return token, False
    return secrets.token_urlsafe(24), True


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the phone-friendly visa bot control server.")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Host to bind. Use 0.0.0.0 for phone access on your network.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port to listen on.")
    args = parser.parse_args()

    token, generated = _control_token()
    bot = BotProcess([sys.executable, "-m", "src.main"], PROJECT_ROOT)
    server = ThreadingHTTPServer((args.host, args.port), ControlRequestHandler)
    server.state = ControlState(token, bot)  # type: ignore[attr-defined]
    stop_polling = threading.Event()
    polling_thread = threading.Thread(
        target=_poll_telegram_subscribers,
        args=(stop_polling,),
        daemon=True,
    )
    polling_thread.start()

    visible_host = _local_ip() if args.host in {"0.0.0.0", ""} else args.host
    print(f"Control server: http://{visible_host}:{args.port}")
    print(f"Control token: {token}")
    if generated:
        print("Set CONTROL_TOKEN in .env to keep this token stable across restarts.")
    print("Keep this window open while you want phone control available.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping control server.")
    finally:
        stop_polling.set()
        bot.stop()
        server.server_close()
    return 0


def _poll_telegram_subscribers(stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        refresh_telegram_subscribers()
        stop_event.wait(TELEGRAM_POLL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
