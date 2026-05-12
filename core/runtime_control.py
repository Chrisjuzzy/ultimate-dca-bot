from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path


CONTROL_FILE = Path("data") / "runtime_control.json"

DEFAULT_STATE = {
    "enabled": False,
    "updated_at": None,
    "mode": "paper",
    "restart_requested_at": None,
    "restart_nonce": 0,
}


def ensure_control_file() -> None:
    CONTROL_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not CONTROL_FILE.exists():
        save_control(DEFAULT_STATE.copy())


def load_control() -> dict:
    ensure_control_file()
    try:
        payload = json.loads(CONTROL_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        payload = DEFAULT_STATE.copy()

    if not isinstance(payload, dict):
        payload = DEFAULT_STATE.copy()

    state = DEFAULT_STATE.copy()
    state.update(payload)
    return state


def save_control(data: dict) -> None:
    CONTROL_FILE.parent.mkdir(parents=True, exist_ok=True)
    state = DEFAULT_STATE.copy()
    state.update(data)
    state["updated_at"] = datetime.now(UTC).isoformat()

    payload = json.dumps(state, indent=2)
    temp_file = CONTROL_FILE.with_suffix(".tmp")
    try:
        temp_file.write_text(payload, encoding="utf-8")
        temp_file.replace(CONTROL_FILE)
    except PermissionError:
        # OneDrive/Windows can briefly lock the target during replace. Direct
        # write is acceptable here because this is a small operator control file.
        CONTROL_FILE.write_text(payload, encoding="utf-8")
        try:
            temp_file.unlink(missing_ok=True)
        except OSError:
            pass


def enable_bot() -> dict:
    state = load_control()
    state["enabled"] = True
    save_control(state)
    return load_control()


def disable_bot() -> dict:
    state = load_control()
    state["enabled"] = False
    save_control(state)
    return load_control()


def set_mode(mode: str) -> dict:
    state = load_control()
    state["mode"] = mode
    save_control(state)
    return load_control()


def request_restart() -> dict:
    state = load_control()
    state["restart_nonce"] = int(state.get("restart_nonce", 0) or 0) + 1
    state["restart_requested_at"] = datetime.now(UTC).isoformat()
    save_control(state)
    return load_control()


def bot_enabled() -> bool:
    return bool(load_control().get("enabled", False))


def restart_nonce() -> int:
    return int(load_control().get("restart_nonce", 0) or 0)
