from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.config_loader import PROJECT_ROOT


MANUAL_ACTION_PATH = PROJECT_ROOT / "logs" / "manual_action.json"
MANUAL_ACTION_IMAGE_PATH = PROJECT_ROOT / "logs" / "manual_action.png"


def create_manual_action_request(title: str, message: str, image_path: Path | None) -> str:
    request_id = uuid4().hex
    payload = {
        "id": request_id,
        "title": title,
        "message": message,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "image_path": str(image_path) if image_path else "",
        "image_mtime": image_path.stat().st_mtime if image_path and image_path.exists() else 0,
    }
    _write_json(MANUAL_ACTION_PATH, payload)
    return request_id


def load_manual_action_request() -> dict[str, Any] | None:
    return _read_json(MANUAL_ACTION_PATH)


def clear_manual_action_request(request_id: str | None = None) -> None:
    payload = _read_json(MANUAL_ACTION_PATH)
    if request_id and payload and payload.get("id") != request_id:
        return
    _delete_file(MANUAL_ACTION_PATH)


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
