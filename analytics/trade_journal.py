import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable, Literal
from uuid import uuid4


JournalEventType = Literal[
    "entry_decision",
    "entry_rejected",
    "exit_decision",
    "order_submitted",
    "order_filled",
    "position_opened",
    "position_scaled",
    "position_closed",
    "paper_fill",
    "risk_event",
    "system_event",
]


@dataclass(frozen=True)
class JournalEntry:
    event_id: str
    event_type: JournalEventType
    timestamp: str
    symbol: str = ""
    status: str = ""
    side: str = ""
    quantity: float = 0.0
    price: float = 0.0
    pnl_usdt: float = 0.0
    entry_type: str = ""
    exit_reason: str = ""
    score: int = 0
    regime: str = ""
    recovery_mode: str = ""
    risk_state: str = ""
    market_stress_score: int = 0
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    payload: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class JournalSummary:
    total_events: int
    entries_approved: int
    entries_rejected: int
    exits: int
    orders_filled: int
    paper_fills: int
    risk_events: int
    realized_pnl_usdt: float
    warning_count: int
    blocker_count: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class TradeJournalConfig:
    path: Path = Path("data") / "trade_journal.jsonl"


class TradeJournal:
    def __init__(self, config: TradeJournalConfig | None = None) -> None:
        self.config = config or TradeJournalConfig()

    def append(self, entry: JournalEntry | dict) -> JournalEntry:
        journal_entry = coerce_journal_entry(entry)
        self.config.path.parent.mkdir(parents=True, exist_ok=True)
        with self.config.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(journal_entry.to_dict(), ensure_ascii=True) + "\n")
        return journal_entry

    def record(
        self,
        event_type: JournalEventType,
        symbol: str = "",
        status: str = "",
        payload: dict | None = None,
        **kwargs,
    ) -> JournalEntry:
        entry = build_journal_entry(
            event_type=event_type,
            symbol=symbol,
            status=status,
            payload=payload or {},
            **kwargs,
        )
        return self.append(entry)

    def load(self, limit: int | None = None) -> list[JournalEntry]:
        entries = load_journal_entries(self.config.path)
        if limit is not None:
            return entries[-limit:]
        return entries

    def filter(
        self,
        symbol: str | None = None,
        event_type: JournalEventType | None = None,
        status: str | None = None,
    ) -> list[JournalEntry]:
        entries = self.load()
        if symbol is not None:
            entries = [entry for entry in entries if entry.symbol == symbol]
        if event_type is not None:
            entries = [entry for entry in entries if entry.event_type == event_type]
        if status is not None:
            entries = [entry for entry in entries if entry.status == status]
        return entries

    def summarize(self) -> JournalSummary:
        return summarize_journal(self.load())


def build_journal_entry(
    event_type: JournalEventType,
    symbol: str = "",
    status: str = "",
    side: str = "",
    quantity: float = 0.0,
    price: float = 0.0,
    pnl_usdt: float = 0.0,
    entry_type: str = "",
    exit_reason: str = "",
    score: int = 0,
    regime: str = "",
    recovery_mode: str = "",
    risk_state: str = "",
    market_stress_score: int = 0,
    reasons: list[str] | None = None,
    warnings: list[str] | None = None,
    blockers: list[str] | None = None,
    payload: dict | None = None,
    timestamp: datetime | str | None = None,
    event_id: str | None = None,
) -> JournalEntry:
    return JournalEntry(
        event_id=event_id or uuid4().hex,
        event_type=event_type,
        timestamp=_to_iso(timestamp),
        symbol=symbol,
        status=status,
        side=side,
        quantity=round(max(0.0, quantity), 8),
        price=round(max(0.0, price), 8),
        pnl_usdt=round(pnl_usdt, 8),
        entry_type=entry_type,
        exit_reason=exit_reason,
        score=score,
        regime=regime,
        recovery_mode=recovery_mode,
        risk_state=risk_state,
        market_stress_score=market_stress_score,
        reasons=list(reasons or []),
        warnings=list(warnings or []),
        blockers=list(blockers or []),
        payload=dict(payload or {}),
    )


def journal_entry_from_entry_decision(decision: dict) -> JournalEntry:
    event_type: JournalEventType = (
        "entry_decision" if decision.get("can_enter") else "entry_rejected"
    )
    return build_journal_entry(
        event_type=event_type,
        symbol=decision.get("symbol", ""),
        status=decision.get("status", ""),
        quantity=_safe_float(decision.get("approved_size", 0.0)),
        entry_type=decision.get("entry_type", ""),
        score=int(decision.get("score", 0)),
        recovery_mode=decision.get("mode", ""),
        market_stress_score=int(decision.get("market_stress_score", 0)),
        reasons=list(decision.get("reasons", [])),
        warnings=list(decision.get("warnings", [])),
        blockers=list(decision.get("blockers", [])),
        payload=decision,
    )


def journal_entry_from_exit_decision(decision: dict) -> JournalEntry:
    return build_journal_entry(
        event_type="exit_decision",
        symbol=decision.get("symbol", ""),
        status=decision.get("action", ""),
        quantity=_safe_float(decision.get("quantity_to_exit", 0.0)),
        price=_safe_float(decision.get("estimated_exit_value_usdt", 0.0)),
        pnl_usdt=_safe_float(decision.get("profit_percent", 0.0)),
        exit_reason=decision.get("exit_reason", ""),
        reasons=list(decision.get("reasons", [])),
        warnings=list(decision.get("warnings", [])),
        blockers=list(decision.get("blockers", [])),
        payload=decision,
    )


def load_journal_entries(path: Path) -> list[JournalEntry]:
    if not path.exists():
        return []
    entries = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            entries.append(coerce_journal_entry(payload))
    return entries


def summarize_journal(entries: Iterable[JournalEntry | dict]) -> JournalSummary:
    journal_entries = [coerce_journal_entry(entry) for entry in entries]
    return JournalSummary(
        total_events=len(journal_entries),
        entries_approved=sum(
            1
            for entry in journal_entries
            if entry.event_type == "entry_decision" and entry.status != "blocked"
        ),
        entries_rejected=sum(
            1
            for entry in journal_entries
            if entry.event_type == "entry_rejected"
            or (entry.event_type == "entry_decision" and entry.status == "blocked")
        ),
        exits=sum(1 for entry in journal_entries if entry.event_type == "exit_decision"),
        orders_filled=sum(1 for entry in journal_entries if entry.event_type == "order_filled"),
        paper_fills=sum(1 for entry in journal_entries if entry.event_type == "paper_fill"),
        risk_events=sum(1 for entry in journal_entries if entry.event_type == "risk_event"),
        realized_pnl_usdt=round(sum(entry.pnl_usdt for entry in journal_entries), 8),
        warning_count=sum(len(entry.warnings) for entry in journal_entries),
        blocker_count=sum(len(entry.blockers) for entry in journal_entries),
    )


def coerce_journal_entry(entry: JournalEntry | dict) -> JournalEntry:
    if isinstance(entry, JournalEntry):
        return entry
    return JournalEntry(
        event_id=str(entry.get("event_id", uuid4().hex)),
        event_type=entry.get("event_type", "system_event"),
        timestamp=str(entry.get("timestamp", _to_iso())),
        symbol=str(entry.get("symbol", "")),
        status=str(entry.get("status", "")),
        side=str(entry.get("side", "")),
        quantity=_safe_float(entry.get("quantity", 0.0)),
        price=_safe_float(entry.get("price", 0.0)),
        pnl_usdt=_safe_float(entry.get("pnl_usdt", 0.0)),
        entry_type=str(entry.get("entry_type", "")),
        exit_reason=str(entry.get("exit_reason", "")),
        score=int(entry.get("score", 0)),
        regime=str(entry.get("regime", "")),
        recovery_mode=str(entry.get("recovery_mode", "")),
        risk_state=str(entry.get("risk_state", "")),
        market_stress_score=int(entry.get("market_stress_score", 0)),
        reasons=list(entry.get("reasons", [])),
        warnings=list(entry.get("warnings", [])),
        blockers=list(entry.get("blockers", [])),
        payload=dict(entry.get("payload", {})),
    )


def _to_iso(value: datetime | str | None = None) -> str:
    if value is None:
        return datetime.now(UTC).isoformat()
    if isinstance(value, str):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _safe_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
