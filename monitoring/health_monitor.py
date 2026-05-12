from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import os
from pathlib import Path
import re
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from analytics.trade_journal import TradeJournal
from utils.state import POSITIONS_FILE, load_positions_state


@dataclass(frozen=True)
class HealthConfig:
    log_file: Path = Path("logs") / "bot.log"
    database_file: Path = Path("database") / "trading.db"
    journal_file: Path = Path("data") / "trade_journal.jsonl"
    max_log_silence_minutes: int = 15
    max_error_count: int = 10


@dataclass(frozen=True)
class HealthReport:
    status: str
    generated_at: str
    log_ready: bool
    database_ready: bool
    positions_ready: bool
    journal_ready: bool
    log_age_minutes: float | None
    error_count: int
    reconnect_count: int
    journal_events: int
    open_positions: int
    uptime_minutes: float | None
    api_status: str
    memory_mb: float | None
    warnings: list[str]
    blockers: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def build_health_report(config: HealthConfig | None = None) -> HealthReport:
    config = config or HealthConfig()
    warnings: list[str] = []
    blockers: list[str] = []

    log_ready = config.log_file.exists()
    database_ready = config.database_file.exists()
    positions_ready = POSITIONS_FILE.exists()
    journal_ready = config.journal_file.exists()
    log_age = file_age_minutes(config.log_file) if log_ready else None
    log_text = tail_text(config.log_file, max_chars=20000).lower() if log_ready else ""
    error_count = log_text.count("error")
    reconnect_count = log_text.count("connection restored")
    journal_events = len(TradeJournal().load()) if journal_ready else 0
    positions = load_positions_state().get("positions", {}) if positions_ready else {}
    open_positions = len(positions) if isinstance(positions, dict) else 0
    uptime_minutes = estimate_uptime_minutes(config.log_file) if log_ready else None
    api_status = infer_api_status(log_text)
    memory_mb = current_process_memory_mb()

    if not log_ready:
        warnings.append("Log file is missing")
    if not database_ready:
        warnings.append("Database file is missing")
    if not positions_ready:
        warnings.append("Positions file is missing")
    if log_age is not None and log_age > config.max_log_silence_minutes:
        warnings.append(f"Log file has been silent for {log_age:.1f} minutes")
    if error_count >= config.max_error_count:
        blockers.append(f"High error count in recent logs: {error_count}")
    if api_status in {"auth_issue", "exchange_unstable"}:
        blockers.append(f"API status requires attention: {api_status}")

    status = "green"
    if warnings:
        status = "yellow"
    if blockers:
        status = "red"

    return HealthReport(
        status=status,
        generated_at=datetime.now(UTC).isoformat(),
        log_ready=log_ready,
        database_ready=database_ready,
        positions_ready=positions_ready,
        journal_ready=journal_ready,
        log_age_minutes=round(log_age, 2) if log_age is not None else None,
        error_count=error_count,
        reconnect_count=reconnect_count,
        journal_events=journal_events,
        open_positions=open_positions,
        uptime_minutes=round(uptime_minutes, 2) if uptime_minutes is not None else None,
        api_status=api_status,
        memory_mb=round(memory_mb, 2) if memory_mb is not None else None,
        warnings=warnings,
        blockers=blockers,
    )


def file_age_minutes(path: Path) -> float:
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    return (datetime.now(UTC) - modified).total_seconds() / 60


def tail_text(path: Path, max_chars: int = 20000) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="ignore")
    return text[-max_chars:]


def estimate_uptime_minutes(path: Path) -> float | None:
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    timestamps = [parse_log_timestamp(line) for line in lines]
    timestamps = [item for item in timestamps if item is not None]
    if not timestamps:
        return None
    return (timestamps[-1] - timestamps[0]).total_seconds() / 60


def parse_log_timestamp(line: str) -> datetime | None:
    match = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return None


def infer_api_status(log_text: str) -> str:
    if not log_text:
        return "unknown"
    if "authentication" in log_text or "invalid api" in log_text:
        return "auth_issue"
    if "exchange error" in log_text or "exchange unavailable" in log_text:
        return "exchange_unstable"
    if "connection restored" in log_text or "usdt balance" in log_text:
        return "healthy"
    if "network" in log_text or "connection lost" in log_text:
        return "network_issue"
    return "unknown"


def current_process_memory_mb() -> float | None:
    try:
        import psutil  # type: ignore

        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    except Exception:
        pass

    try:
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(ProcessMemoryCounters)
        kernel32 = ctypes.WinDLL("kernel32.dll")
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        handle = kernel32.GetCurrentProcess()
        psapi = ctypes.WinDLL("psapi.dll")
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ProcessMemoryCounters),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        ok = psapi.GetProcessMemoryInfo(
            handle,
            ctypes.byref(counters),
            counters.cb,
        )
        if ok:
            return counters.WorkingSetSize / (1024 * 1024)
    except Exception:
        return None

    return None


if __name__ == "__main__":
    report = build_health_report()
    print(report.to_dict())
