import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from utils.logger import logger


POSITIONS_FILE = Path("data") / "positions.json"
_DEFAULT_POSITIONS_STATE = {
    "positions": {},
    "updated_at": None,
}


def _default_positions_state() -> dict:
    return deepcopy(_DEFAULT_POSITIONS_STATE)


def ensure_positions_file() -> Path:
    POSITIONS_FILE.parent.mkdir(parents=True, exist_ok=True)

    if not POSITIONS_FILE.exists():
        save_positions_state(_default_positions_state())
        logger.info("Created positions state file at %s", POSITIONS_FILE)

    return POSITIONS_FILE


def _normalize_positions_payload(raw_state: Any) -> tuple[dict, bool]:
    normalized = False

    if not isinstance(raw_state, dict):
        return _default_positions_state(), True

    if "positions" not in raw_state:
        symbol_map = {
            symbol: payload
            for symbol, payload in raw_state.items()
            if isinstance(symbol, str) and "/" in symbol and isinstance(payload, dict)
        }
        return {
            "positions": symbol_map,
            "updated_at": None,
        }, True

    state = {
        "positions": raw_state.get("positions", {}),
        "updated_at": raw_state.get("updated_at"),
    }

    if isinstance(state["positions"], list):
        positions_map = {}
        for item in state["positions"]:
            if not isinstance(item, dict):
                continue
            symbol = item.get("symbol")
            if not symbol:
                continue
            positions_map[str(symbol)] = {
                key: value for key, value in item.items() if key != "symbol"
            }
        state["positions"] = positions_map
        normalized = True
    elif not isinstance(state["positions"], dict):
        state["positions"] = {}
        normalized = True

    return state, normalized


def load_positions_state() -> dict:
    ensure_positions_file()

    try:
        with POSITIONS_FILE.open("r", encoding="utf-8") as file:
            raw_state = json.load(file)
    except json.JSONDecodeError:
        logger.warning("Positions state file is invalid JSON. Resetting %s", POSITIONS_FILE)
        state = _default_positions_state()
        save_positions_state(state)
        return state

    state, normalized = _normalize_positions_payload(raw_state)

    if normalized:
        logger.info("Normalized positions state structure in %s", POSITIONS_FILE)
        save_positions_state(state)

    state.setdefault("positions", {})
    state.setdefault("updated_at", None)
    return state


def save_positions_state(state: dict) -> None:
    payload = {
        "positions": state.get("positions", {}),
        "updated_at": datetime.now(UTC).isoformat(),
    }

    POSITIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with POSITIONS_FILE.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2)


def get_positions_map(state: dict) -> dict[str, dict]:
    positions = state.get("positions", {})
    if isinstance(positions, dict):
        return positions
    return {}


def get_open_positions_count(state: dict) -> int:
    return len(get_positions_map(state))
