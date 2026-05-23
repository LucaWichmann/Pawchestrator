"""Pairing session token persistence."""

from __future__ import annotations

import json
import secrets
import threading
from pathlib import Path
from typing import Any

from pawchestrator.config import Settings, ensure_app_dir

_pair_lock = threading.Lock()


def generate_token() -> str:
    """Return a 32-byte random token encoded as hex."""

    return secrets.token_hex(32)


def load_sessions(settings: Settings) -> dict[str, Any]:
    """Load persisted pairing sessions."""

    path = settings.sessions_path
    if not path.exists():
        return {"tokens": []}

    with path.open("r", encoding="utf-8") as sessions_file:
        data = json.load(sessions_file)

    tokens = data.get("tokens", [])
    if not isinstance(tokens, list):
        return {"tokens": []}
    return {"tokens": [token for token in tokens if isinstance(token, str)]}


def save_sessions(settings: Settings, data: dict[str, Any]) -> None:
    """Atomically write pairing sessions to disk."""

    ensure_app_dir(settings)
    path = settings.sessions_path
    temp_path = path.with_suffix(f"{path.suffix}.tmp")
    payload = json.dumps(data, indent=2, sort_keys=True)

    with temp_path.open("w", encoding="utf-8") as sessions_file:
        sessions_file.write(payload)
        sessions_file.write("\n")

    try:
        temp_path.chmod(0o600)
    except OSError:
        pass
    temp_path.replace(path)


def token_exists(settings: Settings, token: str) -> bool:
    """Return whether a token is currently paired."""

    sessions = load_sessions(settings)
    return token in sessions["tokens"]
