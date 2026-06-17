from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.config_loader import PROJECT_ROOT


SECURITY_REQUEST_PATH = PROJECT_ROOT / "logs" / "security_challenge.json"
SECURITY_ANSWER_PATH = PROJECT_ROOT / "logs" / "security_answer.json"
SECURITY_IMAGE_PATH = PROJECT_ROOT / "logs" / "security_challenge.png"


def create_security_request(label: str, image_path: Path | None) -> str:
    challenge_id = uuid4().hex
    payload = {
        "id": challenge_id,
        "label": label,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "image_path": str(image_path) if image_path else "",
        "image_mtime": image_path.stat().st_mtime if image_path and image_path.exists() else 0,
    }
    _write_json(SECURITY_REQUEST_PATH, payload)
    _delete_file(SECURITY_ANSWER_PATH)
    return challenge_id


def load_security_request() -> dict[str, Any] | None:
    return _read_json(SECURITY_REQUEST_PATH)


def submit_security_answer(challenge_id: str, answer: str) -> None:
    _write_json(
        SECURITY_ANSWER_PATH,
        {
            "id": challenge_id,
            "answer": answer,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        },
    )


def consume_security_answer(challenge_id: str) -> str | None:
    payload = _read_json(SECURITY_ANSWER_PATH)
    if not payload or payload.get("id") != challenge_id:
        return None
    answer = str(payload.get("answer", "")).strip()
    _delete_file(SECURITY_ANSWER_PATH)
    return answer or None


def clear_security_request(challenge_id: str | None = None) -> None:
    payload = _read_json(SECURITY_REQUEST_PATH)
    if challenge_id and payload and payload.get("id") != challenge_id:
        return
    _delete_file(SECURITY_REQUEST_PATH)
    _delete_file(SECURITY_ANSWER_PATH)


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
