from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from src.config_loader import PROJECT_ROOT


TELEGRAM_ALERT_STATE_PATH = PROJECT_ROOT / "logs" / "telegram_last_alert.json"
TELEGRAM_SUBSCRIBERS_PATH = PROJECT_ROOT / "logs" / "telegram_subscribers.json"
TELEGRAM_SUBSCRIBER_POLL_INTERVAL_SECONDS = 60
TELEGRAM_SUBSCRIBER_FAILURE_LOG_INTERVAL_SECONDS = 15 * 60

_last_subscriber_poll_at = 0.0
_last_subscriber_failure_log_at = 0.0
_subscriber_refresh_failure_count = 0


def telegram_enabled() -> bool:
    load_dotenv(PROJECT_ROOT / ".env")
    return bool(_bot_token() and _chat_ids())


def send_telegram_alert(
    *,
    title: str,
    message: str,
    severity: str,
    screenshot_path: Path | None = None,
    force: bool = False,
) -> bool:
    load_dotenv(PROJECT_ROOT / ".env")
    token = _bot_token()
    if token:
        refresh_telegram_subscribers(force=True)
    chat_ids = _chat_ids()
    if not token or not chat_ids:
        return False

    text = _format_alert(title=title, message=message, severity=severity)
    alert_key = hashlib.sha256(f"{severity}|{title}|{message}".encode("utf-8")).hexdigest()
    if not force and not _should_send(alert_key):
        return False

    sent = False
    for chat_id in chat_ids:
        try:
            if screenshot_path and screenshot_path.exists() and screenshot_path.is_file():
                _send_photo(token, chat_id, screenshot_path, text)
            else:
                _send_message(token, chat_id, text)
            sent = True
        except (OSError, urllib.error.URLError, urllib.error.HTTPError):
            continue

    if sent:
        _record_sent(alert_key)
        return True
    return False


def refresh_telegram_subscribers(*, force: bool = False, logger: Any | None = None) -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    token = _bot_token()
    if not token:
        return 0

    global _last_subscriber_poll_at
    now = time.time()
    if not force and now - _last_subscriber_poll_at < TELEGRAM_SUBSCRIBER_POLL_INTERVAL_SECONDS:
        return len(_subscribed_chat_ids())
    _last_subscriber_poll_at = now

    state = _read_subscriber_state()
    offset = int(state.get("last_update_id", 0)) + 1 if state.get("last_update_id") else None
    try:
        updates = _get_updates(token, offset=offset)
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        _log_subscriber_refresh_failure(exc, logger)
        return len(_subscribed_chat_ids(state))

    _clear_subscriber_refresh_failures(logger)
    changed = False
    subscribers = state.setdefault("subscribers", {})
    for update in updates:
        update_id = update.get("update_id")
        if isinstance(update_id, int):
            state["last_update_id"] = max(int(state.get("last_update_id", 0)), update_id)

        message = update.get("message") or update.get("edited_message") or {}
        text = str(message.get("text", "")).strip()
        command = _telegram_command(text)
        if command not in {"/start", "/stop"}:
            continue

        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", "")).strip()
        if not chat_id:
            continue

        if command == "/start":
            already_subscribed = chat_id in subscribers
            subscribers[chat_id] = _subscriber_record(chat)
            changed = True
            if not already_subscribed:
                _try_send_message(
                    token,
                    chat_id,
                    "You are subscribed to visa bot alerts. Send /stop to unsubscribe.",
                )
                if logger:
                    logger.info("telegram_subscriber_added | chat_id=%s", chat_id)
        elif command == "/stop" and chat_id in subscribers:
            del subscribers[chat_id]
            changed = True
            _try_send_message(token, chat_id, "You are unsubscribed from visa bot alerts.")
            if logger:
                logger.info("telegram_subscriber_removed | chat_id=%s", chat_id)

    if changed or updates:
        _write_subscriber_state(state)
    return len(_subscribed_chat_ids(state))


def _log_subscriber_refresh_failure(exc: Exception, logger: Any | None) -> None:
    if not logger:
        return

    global _last_subscriber_failure_log_at, _subscriber_refresh_failure_count
    _subscriber_refresh_failure_count += 1
    now = time.time()
    if _subscriber_refresh_failure_count < 5:
        logger.info(
            "telegram_subscriber_refresh_deferred | failures=%s | error=%s",
            _subscriber_refresh_failure_count,
            str(exc).splitlines()[0][:220],
        )
        return

    if now - _last_subscriber_failure_log_at < TELEGRAM_SUBSCRIBER_FAILURE_LOG_INTERVAL_SECONDS:
        logger.info(
            "telegram_subscriber_refresh_deferred | failures=%s | error=%s",
            _subscriber_refresh_failure_count,
            str(exc).splitlines()[0][:220],
        )
        return

    _last_subscriber_failure_log_at = now
    logger.warning(
        "telegram_subscriber_refresh_failed | failures=%s | using_cached_recipients=true | %s",
        _subscriber_refresh_failure_count,
        str(exc).splitlines()[0][:220],
    )


def _clear_subscriber_refresh_failures(logger: Any | None) -> None:
    global _last_subscriber_failure_log_at, _subscriber_refresh_failure_count
    if _subscriber_refresh_failure_count and logger:
        logger.info(
            "telegram_subscriber_refresh_recovered | previous_failures=%s",
            _subscriber_refresh_failure_count,
        )
    _subscriber_refresh_failure_count = 0
    _last_subscriber_failure_log_at = 0.0


def telegram_subscriber_chat_ids() -> list[str]:
    load_dotenv(PROJECT_ROOT / ".env")
    return _chat_ids()


def _format_alert(*, title: str, message: str, severity: str) -> str:
    return "\n".join(
        [
            f"Visa bot alert: {title}",
            f"Severity: {severity}",
            "",
            message,
            "",
            "Open the phone control page for status and logs.",
        ]
    )


def _send_message(token: str, chat_id: str, text: str) -> None:
    payload = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        _telegram_url(token, "sendMessage"),
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        response.read()


def _try_send_message(token: str, chat_id: str, text: str) -> bool:
    try:
        _send_message(token, chat_id, text)
        return True
    except (OSError, urllib.error.URLError, urllib.error.HTTPError):
        return False


def _send_photo(token: str, chat_id: str, photo_path: Path, caption: str) -> None:
    boundary = f"----visa-bot-{int(time.time() * 1000)}"
    mime_type = mimetypes.guess_type(photo_path.name)[0] or "application/octet-stream"
    body = b"".join(
        [
            _multipart_field(boundary, "chat_id", chat_id),
            _multipart_field(boundary, "caption", caption[:1024]),
            _multipart_file(boundary, "photo", photo_path, mime_type),
            f"--{boundary}--\r\n".encode("utf-8"),
        ]
    )
    request = urllib.request.Request(
        _telegram_url(token, "sendPhoto"),
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        response.read()


def _multipart_field(boundary: str, name: str, value: str) -> bytes:
    return (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
        f"{value}\r\n"
    ).encode("utf-8")


def _multipart_file(boundary: str, name: str, path: Path, mime_type: str) -> bytes:
    header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{name}"; filename="{path.name}"\r\n'
        f"Content-Type: {mime_type}\r\n\r\n"
    ).encode("utf-8")
    return header + path.read_bytes() + b"\r\n"


def _telegram_url(token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{token}/{method}"


def _bot_token() -> str:
    return os.getenv("TELEGRAM_BOT_TOKEN", "").strip()


def _chat_ids() -> list[str]:
    values = [
        os.getenv("TELEGRAM_CHAT_ID", ""),
        os.getenv("TELEGRAM_CHAT_IDS", ""),
    ]
    chat_ids: list[str] = []
    seen: set[str] = set()
    for value in values:
        for chat_id in re.split(r"[\s,;]+", value.strip()):
            if chat_id and chat_id not in seen:
                chat_ids.append(chat_id)
                seen.add(chat_id)
    for chat_id in _subscribed_chat_ids():
        if chat_id not in seen:
            chat_ids.append(chat_id)
            seen.add(chat_id)
    return chat_ids


def _subscribed_chat_ids(state: dict[str, Any] | None = None) -> list[str]:
    state = state or _read_subscriber_state()
    subscribers = state.get("subscribers", {})
    if not isinstance(subscribers, dict):
        return []
    return [str(chat_id) for chat_id in subscribers if str(chat_id).strip()]


def _get_updates(token: str, *, offset: int | None = None) -> list[dict[str, Any]]:
    params: dict[str, str] = {
        "timeout": "0",
        "allowed_updates": json.dumps(["message", "edited_message"]),
    }
    if offset is not None:
        params["offset"] = str(offset)
    query = urllib.parse.urlencode(params)
    with urllib.request.urlopen(f"{_telegram_url(token, 'getUpdates')}?{query}", timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not payload.get("ok", False):
        return []
    updates = payload.get("result", [])
    return updates if isinstance(updates, list) else []


def _telegram_command(text: str) -> str:
    if not text.startswith("/"):
        return ""
    command = text.split(maxsplit=1)[0]
    return command.split("@", 1)[0].lower()


def _subscriber_record(chat: dict[str, Any]) -> dict[str, str]:
    name = " ".join(
        str(chat.get(part, "")).strip()
        for part in ("first_name", "last_name", "title")
        if chat.get(part)
    )
    return {
        "chat_id": str(chat.get("id", "")).strip(),
        "type": str(chat.get("type", "")).strip(),
        "name": name,
        "username": str(chat.get("username", "")).strip(),
        "joined_at": datetime.now().isoformat(timespec="seconds"),
    }


def _read_subscriber_state() -> dict[str, Any]:
    payload = _read_json(TELEGRAM_SUBSCRIBERS_PATH)
    if not payload:
        return {"last_update_id": 0, "subscribers": {}}
    payload.setdefault("last_update_id", 0)
    if not isinstance(payload.get("subscribers"), dict):
        payload["subscribers"] = {}
    return payload


def _write_subscriber_state(state: dict[str, Any]) -> None:
    TELEGRAM_SUBSCRIBERS_PATH.parent.mkdir(exist_ok=True)
    tmp = TELEGRAM_SUBSCRIBERS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=True, indent=2), encoding="utf-8")
    tmp.replace(TELEGRAM_SUBSCRIBERS_PATH)


def _should_send(alert_key: str) -> bool:
    throttle_seconds = int(os.getenv("TELEGRAM_ALERT_THROTTLE_SECONDS", "300") or "300")
    payload = _read_json(TELEGRAM_ALERT_STATE_PATH)
    if not payload:
        return True
    if payload.get("key") != alert_key:
        return True
    return time.time() - float(payload.get("sent_at", 0)) >= throttle_seconds


def _record_sent(alert_key: str) -> None:
    TELEGRAM_ALERT_STATE_PATH.parent.mkdir(exist_ok=True)
    tmp = TELEGRAM_ALERT_STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps({"key": alert_key, "sent_at": time.time()}, ensure_ascii=True),
        encoding="utf-8",
    )
    tmp.replace(TELEGRAM_ALERT_STATE_PATH)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return None
