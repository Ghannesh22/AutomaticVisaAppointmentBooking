from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.config_loader import PROJECT_ROOT


ERROR_ALERT_PATH = PROJECT_ROOT / "logs" / "phone_error_alert.json"


def create_error_alert(
    *,
    title: str,
    message: str,
    severity: str,
    screenshot_path: Path | None = None,
    retry_count: int | None = None,
    next_wait_seconds: int | None = None,
) -> str:
    alert_id = uuid4().hex
    payload = {
        "id": alert_id,
        "title": title,
        "message": message,
        "severity": severity,
        "retry_count": retry_count,
        "next_wait_seconds": next_wait_seconds,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "screenshot_path": str(screenshot_path) if screenshot_path else "",
        "screenshot_mtime": screenshot_path.stat().st_mtime
        if screenshot_path and screenshot_path.exists()
        else 0,
    }
    _write_json(ERROR_ALERT_PATH, payload)
    return alert_id


def load_error_alert() -> dict[str, Any] | None:
    return _read_json(ERROR_ALERT_PATH)


def clear_error_alert() -> None:
    _delete_file(ERROR_ALERT_PATH)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
    tmp.replace(path)


def _delete_file(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
