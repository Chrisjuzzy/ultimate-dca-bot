from pathlib import Path


def env_to_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def tail_lines(path: str | Path, limit: int = 50) -> str:
    file_path = Path(path)
    if not file_path.exists():
        return ""

    with file_path.open("r", encoding="utf-8") as file:
        lines = file.readlines()

    return "".join(lines[-limit:])
