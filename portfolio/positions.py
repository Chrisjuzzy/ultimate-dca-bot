from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from analytics.trade_history import TradeHistoryManager
from execution.entries import EntryDecision
from execution.exits import ExitDecision
from utils.logger import logger
from utils.state import load_positions_state, save_positions_state


def clamp_float(value: float, min_val: float, max_val: float) -> float:
    """Clamp a float value between min and max."""
    return max(min_val, min(max_val, value))


def clamp_int(value: int, min_val: int, max_val: int) -> int:
    """Clamp an integer value between min and max."""
    return max(min_val, min(max_val, int(value)))


PositionStatus = Literal["open", "closed"]
PositionEventType = Literal[
    "opened",
    "scaled",
    "partial_exited",
    "trailing_updated",
    "closed",
    "price_updated",
    "fee_recorded",
]


@dataclass(frozen=True)
class PositionEvent:
    event_type: PositionEventType
    timestamp: str
    quantity: float = 0.0
    price: float = 0.0
    fee_usdt: float = 0.0
    realized_pnl_usdt: float = 0.0
    reason: str = ""
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Position:
    symbol: str
    status: PositionStatus
    entry_price: float
    current_price: float
    quantity: float
    remaining_quantity: float
    opened_at: str
    entry_type: str = "unknown"
    entry_grade: str = "unknown"
    entry_score: int = 0
    regime_at_entry: str = "unknown"
    stop_loss_price: float | None = None
    trailing_stop_price: float | None = None
    highest_price: float | None = None
    tp1_hit: bool = False
    tp2_hit: bool = False
    emergency_mode: bool = False
    realized_pnl_usdt: float = 0.0
    unrealized_pnl_usdt: float = 0.0
    entry_fee_usdt: float = 0.0
    exit_fee_usdt: float = 0.0
    total_fees_usdt: float = 0.0
    closed_at: str | None = None
    exit_reason: str | None = None
    last_adjustment_at: str | None = None
    events: list[PositionEvent] = field(default_factory=list)
    # Professional metadata for trade history
    entry_reasons: list[str] = field(default_factory=list)
    market_stress_score: int = 0
    volatility_level: str = "normal"
    recovery_mode: str = "normal"
    confidence_level: int = 0

    @property
    def is_open(self) -> bool:
        return self.status == "open"

    @property
    def hold_minutes(self) -> float:
        end = _parse_datetime(self.closed_at) or datetime.now(UTC)
        start = _parse_datetime(self.opened_at)
        if start is None:
            return 0.0
        return max(0.0, (end - start).total_seconds() / 60)

    @property
    def hold_hours(self) -> float:
        return self.hold_minutes / 60

    @property
    def partial_exits_taken(self) -> tuple[str, ...]:
        labels = []
        if self.tp1_hit:
            labels.append("tp1")
        if self.tp2_hit:
            labels.append("tp2")
        return tuple(labels)

    def to_dict(self) -> dict:
        payload = {
            "symbol": self.symbol,
            "status": self.status,
            "entry_price": self.entry_price,
            "current_price": self.current_price,
            "quantity": self.quantity,
            "remaining_quantity": self.remaining_quantity,
            "opened_at": self.opened_at,
            "entry_type": self.entry_type,
            "entry_grade": self.entry_grade,
            "entry_score": self.entry_score,
            "regime_at_entry": self.regime_at_entry,
            "stop_loss_price": self.stop_loss_price,
            "trailing_stop_price": self.trailing_stop_price,
            "highest_price": self.highest_price,
            "tp1_hit": self.tp1_hit,
            "tp2_hit": self.tp2_hit,
            "emergency_mode": self.emergency_mode,
            "realized_pnl_usdt": self.realized_pnl_usdt,
            "unrealized_pnl_usdt": self.unrealized_pnl_usdt,
            "entry_fee_usdt": self.entry_fee_usdt,
            "exit_fee_usdt": self.exit_fee_usdt,
            "total_fees_usdt": self.total_fees_usdt,
            "closed_at": self.closed_at,
            "exit_reason": self.exit_reason,
            "last_adjustment_at": self.last_adjustment_at,
            "entry_reasons": self.entry_reasons,
            "market_stress_score": self.market_stress_score,
            "volatility_level": self.volatility_level,
            "recovery_mode": self.recovery_mode,
            "confidence_level": self.confidence_level,
            "events": [
                event.to_dict() if isinstance(event, PositionEvent) else dict(event)
                for event in self.events
            ],
        }
        payload["hold_minutes"] = round(self.hold_minutes, 4)
        payload["hold_hours"] = round(self.hold_hours, 4)
        payload["partial_exits_taken"] = list(self.partial_exits_taken)
        return payload


@dataclass(frozen=True)
class PositionSnapshot:
    positions: dict[str, Position]
    updated_at: str | None = None

    @property
    def open_positions(self) -> dict[str, Position]:
        return {
            symbol: position
            for symbol, position in self.positions.items()
            if position.status == "open"
        }

    @property
    def closed_positions(self) -> dict[str, Position]:
        return {
            symbol: position
            for symbol, position in self.positions.items()
            if position.status == "closed"
        }

    @property
    def total_unrealized_pnl_usdt(self) -> float:
        return round(
            sum(position.unrealized_pnl_usdt for position in self.open_positions.values()),
            8,
        )

    @property
    def total_realized_pnl_usdt(self) -> float:
        return round(
            sum(position.realized_pnl_usdt for position in self.positions.values()),
            8,
        )

    @property
    def total_fees_usdt(self) -> float:
        return round(sum(position.total_fees_usdt for position in self.positions.values()), 8)

    def to_dict(self) -> dict:
        return {
            "positions": {
                symbol: position.to_dict()
                for symbol, position in self.positions.items()
            },
            "updated_at": self.updated_at,
            "summary": {
                "open_count": len(self.open_positions),
                "closed_count": len(self.closed_positions),
                "total_unrealized_pnl_usdt": self.total_unrealized_pnl_usdt,
                "total_realized_pnl_usdt": self.total_realized_pnl_usdt,
                "total_fees_usdt": self.total_fees_usdt,
            },
        }


class PositionManager:
    def __init__(self, state_path: Path | None = None) -> None:
        self.state_path = state_path
        self._state: dict | None = None

    def load(self) -> PositionSnapshot:
        if self.state_path is not None:
            state = load_position_state_from_path(self.state_path)
        else:
            state = load_positions_state()
        self._state = state
        return snapshot_from_state(state)

    def save(self, snapshot: PositionSnapshot) -> None:
        state = snapshot_to_state(snapshot)
        self._state = state
        if self.state_path is not None:
            save_position_state_to_path(state, self.state_path)
        else:
            save_positions_state(state)

    def open_position(
        self,
        symbol: str,
        entry_price: float,
        quantity: float,
        entry_decision: EntryDecision | dict | None = None,
        fee_usdt: float = 0.0,
        timestamp: datetime | str | None = None,
        entry_reasons: list[str] | None = None,
        market_stress_score: int = 0,
        volatility_level: str = "normal",
        recovery_mode: str = "normal",
        confidence_level: int = 0,
    ) -> Position:
        snapshot = self.load()
        position = open_position(
            symbol=symbol,
            entry_price=entry_price,
            quantity=quantity,
            entry_decision=entry_decision,
            fee_usdt=fee_usdt,
            timestamp=timestamp,
            entry_reasons=entry_reasons,
            market_stress_score=market_stress_score,
            volatility_level=volatility_level,
            recovery_mode=recovery_mode,
            confidence_level=confidence_level,
        )
        positions = dict(snapshot.positions)
        positions[symbol] = position
        updated = PositionSnapshot(positions=positions, updated_at=_now_iso())
        self.save(updated)
        return position

    def scale_position(
        self,
        symbol: str,
        add_quantity: float,
        price: float,
        fee_usdt: float = 0.0,
        reason: str = "scale in",
        timestamp: datetime | str | None = None,
    ) -> Position:
        snapshot = self.load()
        position = require_position(snapshot, symbol)
        updated_position = scale_position(
            position=position,
            add_quantity=add_quantity,
            price=price,
            fee_usdt=fee_usdt,
            reason=reason,
            timestamp=timestamp,
        )
        self._save_position(snapshot, updated_position)
        return updated_position

    def update_price(
        self,
        symbol: str,
        current_price: float,
        timestamp: datetime | str | None = None,
    ) -> Position:
        snapshot = self.load()
        position = require_position(snapshot, symbol)
        updated_position = update_position_price(position, current_price, timestamp=timestamp)
        self._save_position(snapshot, updated_position)
        return updated_position

    def apply_exit(
        self,
        symbol: str,
        exit_decision: ExitDecision | dict,
        fill_price: float | None = None,
        fee_usdt: float = 0.0,
        timestamp: datetime | str | None = None,
        trade_history_manager=None,
    ) -> Position:
        snapshot = self.load()
        position = require_position(snapshot, symbol)
        updated_position = apply_exit_decision(
            position=position,
            exit_decision=exit_decision,
            fill_price=fill_price,
            fee_usdt=fee_usdt,
            timestamp=timestamp,
            trade_history_manager=trade_history_manager,
        )
        self._save_position(snapshot, updated_position)
        return updated_position

    def _save_position(self, snapshot: PositionSnapshot, position: Position) -> None:
        positions = dict(snapshot.positions)
        positions[position.symbol] = position
        self.save(PositionSnapshot(positions=positions, updated_at=_now_iso()))


def open_position(
    symbol: str,
    entry_price: float,
    quantity: float,
    entry_decision: EntryDecision | dict | None = None,
    fee_usdt: float = 0.0,
    timestamp: datetime | str | None = None,
    entry_reasons: list[str] | None = None,
    market_stress_score: int = 0,
    volatility_level: str = "normal",
    recovery_mode: str = "normal",
    confidence_level: int = 0,
) -> Position:
    opened_at = _to_iso(timestamp)
    decision = _coerce_entry_decision(entry_decision)
    entry_type = str(decision.get("entry_type", "unknown"))
    entry_grade = str(decision.get("entry_grade", "unknown"))
    entry_score = int(decision.get("score", 0))
    regime_at_entry = str(decision.get("regime", decision.get("mode", "unknown")))
    quantity = max(0.0, quantity)
    entry_price = max(0.0, entry_price)
    fee_usdt = max(0.0, fee_usdt)
    market_stress_score = clamp_int(market_stress_score, 0, 100)
    confidence_level = clamp_int(confidence_level, 0, 100)
    entry_reasons = entry_reasons or []

    event = PositionEvent(
        event_type="opened",
        timestamp=opened_at,
        quantity=quantity,
        price=entry_price,
        fee_usdt=fee_usdt,
        reason="Position opened",
        metadata={
            "entry_type": entry_type,
            "entry_grade": entry_grade,
            "entry_score": entry_score,
            "entry_reasons": entry_reasons,
            "market_stress_score": market_stress_score,
            "volatility_level": volatility_level,
            "recovery_mode": recovery_mode,
            "confidence_level": confidence_level,
        },
    )

    position = Position(
        symbol=symbol,
        status="open",
        entry_price=entry_price,
        current_price=entry_price,
        quantity=quantity,
        remaining_quantity=quantity,
        opened_at=opened_at,
        entry_type=entry_type,
        entry_grade=entry_grade,
        entry_score=entry_score,
        regime_at_entry=regime_at_entry,
        highest_price=entry_price,
        realized_pnl_usdt=0.0,
        unrealized_pnl_usdt=0.0,
        entry_fee_usdt=fee_usdt,
        total_fees_usdt=fee_usdt,
        last_adjustment_at=opened_at,
        events=[event],
        entry_reasons=entry_reasons,
        market_stress_score=market_stress_score,
        volatility_level=volatility_level,
        recovery_mode=recovery_mode,
        confidence_level=confidence_level,
    )
    return recalculate_unrealized_pnl(position)


def scale_position(
    position: Position | dict,
    add_quantity: float,
    price: float,
    fee_usdt: float = 0.0,
    reason: str = "scale in",
    timestamp: datetime | str | None = None,
) -> Position:
    current = _coerce_position(position)
    add_quantity = max(0.0, add_quantity)
    price = max(0.0, price)
    fee_usdt = max(0.0, fee_usdt)
    timestamp_iso = _to_iso(timestamp)

    if add_quantity <= 0 or price <= 0:
        return current

    new_remaining = current.remaining_quantity + add_quantity
    new_entry_price = weighted_average_price(
        current.entry_price,
        current.remaining_quantity,
        price,
        add_quantity,
    )
    event = PositionEvent(
        event_type="scaled",
        timestamp=timestamp_iso,
        quantity=add_quantity,
        price=price,
        fee_usdt=fee_usdt,
        reason=reason,
    )

    updated = Position(
        **{
            **asdict(current),
            "entry_price": new_entry_price,
            "current_price": price,
            "quantity": current.quantity + add_quantity,
            "remaining_quantity": new_remaining,
            "highest_price": max(current.highest_price or price, price),
            "entry_fee_usdt": current.entry_fee_usdt + fee_usdt,
            "total_fees_usdt": current.total_fees_usdt + fee_usdt,
            "last_adjustment_at": timestamp_iso,
            "events": current.events + [event],
        }
    )
    return recalculate_unrealized_pnl(updated)


def update_position_price(
    position: Position | dict,
    current_price: float,
    timestamp: datetime | str | None = None,
) -> Position:
    current = _coerce_position(position)
    current_price = max(0.0, current_price)
    timestamp_iso = _to_iso(timestamp)
    highest = max(current.highest_price or current_price, current_price)
    event = PositionEvent(
        event_type="price_updated",
        timestamp=timestamp_iso,
        price=current_price,
        reason="Position price updated",
    )
    updated = Position(
        **{
            **asdict(current),
            "current_price": current_price,
            "highest_price": highest,
            "last_adjustment_at": timestamp_iso,
            "events": current.events + [event],
        }
    )
    return recalculate_unrealized_pnl(updated)


def update_trailing_stop(
    position: Position | dict,
    trailing_stop_price: float,
    reason: str = "trailing stop updated",
    timestamp: datetime | str | None = None,
) -> Position:
    current = _coerce_position(position)
    timestamp_iso = _to_iso(timestamp)
    trailing_stop_price = max(0.0, trailing_stop_price)
    event = PositionEvent(
        event_type="trailing_updated",
        timestamp=timestamp_iso,
        price=trailing_stop_price,
        reason=reason,
    )
    return Position(
        **{
            **asdict(current),
            "trailing_stop_price": trailing_stop_price,
            "last_adjustment_at": timestamp_iso,
            "events": current.events + [event],
        }
    )


def apply_exit_decision(
    position: Position | dict,
    exit_decision: ExitDecision | dict,
    fill_price: float | None = None,
    fee_usdt: float = 0.0,
    timestamp: datetime | str | None = None,
    trade_history_manager=None,
) -> Position:
    """
    Apply an exit decision to a position and optionally record to trade history.
    
    Args:
        position: Current position
        exit_decision: Exit decision object/dict
        fill_price: Price at which trade filled
        fee_usdt: Exit fee
        timestamp: Exit timestamp
        trade_history_manager: Optional TradeHistoryManager instance to record completed trades
    
    Returns:
        Updated position
    """
    from datetime import datetime as dt
    
    current = _coerce_position(position)
    decision = _coerce_exit_decision(exit_decision)
    timestamp_iso = _to_iso(timestamp)

    updated_trailing = decision.get("updated_trailing_stop_price")
    if decision.get("action") == "update_trailing_stop" and updated_trailing is not None:
        return update_trailing_stop(
            current,
            trailing_stop_price=float(updated_trailing),
            reason=decision.get("hold_reason", "trailing stop updated"),
            timestamp=timestamp_iso,
        )

    if not decision.get("should_exit", False):
        return current

    exit_fraction = clamp_float(decision.get("exit_fraction", 0.0), 0.0, 1.0)
    exit_quantity = min(
        current.remaining_quantity,
        float(decision.get("quantity_to_exit", 0.0))
        or current.remaining_quantity * exit_fraction,
    )
    exit_price = fill_price or float(decision.get("fill_price", 0.0)) or current.current_price
    fee_usdt = max(0.0, fee_usdt)

    if exit_quantity <= 0 or exit_price <= 0:
        return current

    realized_pnl = calculate_realized_pnl(
        entry_price=current.entry_price,
        exit_price=exit_price,
        quantity=exit_quantity,
        fee_usdt=fee_usdt,
    )
    remaining = max(0.0, current.remaining_quantity - exit_quantity)
    exit_reason = decision.get("exit_reason") or decision.get("action", "exit")
    action = str(decision.get("action", "partial_exit"))
    is_closed = remaining <= 1e-12 or action in {
        "stop_loss",
        "trailing_stop",
        "emergency_exit",
        "full_exit",
    }

    event_type: PositionEventType = "closed" if is_closed else "partial_exited"
    event = PositionEvent(
        event_type=event_type,
        timestamp=timestamp_iso,
        quantity=exit_quantity,
        price=exit_price,
        fee_usdt=fee_usdt,
        realized_pnl_usdt=realized_pnl,
        reason=exit_reason,
        metadata={"action": action},
    )

    tp1_hit = current.tp1_hit
    tp2_hit = current.tp2_hit
    if action == "partial_take_profit":
        if not tp1_hit:
            tp1_hit = True
        elif not tp2_hit:
            tp2_hit = True

    updated = Position(
        **{
            **asdict(current),
            "status": "closed" if is_closed else "open",
            "current_price": exit_price,
            "remaining_quantity": 0.0 if is_closed else remaining,
            "tp1_hit": tp1_hit,
            "tp2_hit": tp2_hit,
            "emergency_mode": action == "emergency_exit",
            "realized_pnl_usdt": current.realized_pnl_usdt + realized_pnl,
            "exit_fee_usdt": current.exit_fee_usdt + fee_usdt,
            "total_fees_usdt": current.total_fees_usdt + fee_usdt,
            "closed_at": timestamp_iso if is_closed else current.closed_at,
            "exit_reason": exit_reason if is_closed else current.exit_reason,
            "last_adjustment_at": timestamp_iso,
            "events": current.events + [event],
        }
    )
    
    result_position = recalculate_unrealized_pnl(updated)
    
    # Record to trade history if position is closed and manager is provided
    if is_closed and trade_history_manager is not None:
        try:
            from datetime import datetime as dt
            hold_minutes = (dt.fromisoformat(timestamp_iso) - dt.fromisoformat(current.opened_at)).total_seconds() / 60
            
            trade_history_manager.record_trade(
                symbol=current.symbol,
                side="long",  # Assuming long positions for now
                entry_price=current.entry_price,
                exit_price=exit_price,
                quantity=current.quantity,
                pnl_usdt=realized_pnl,
                pnl_pct=(realized_pnl / (current.entry_price * current.quantity)) * 100 if current.entry_price * current.quantity > 0 else 0,
                hold_minutes=int(hold_minutes),
                score=current.entry_score,
                regime=current.regime_at_entry,
                exit_reason=exit_reason,
                entry_reason=current.entry_reasons,
                fees_usdt=current.total_fees_usdt,
                order_id=f"{current.symbol}_{current.opened_at.replace('-', '').replace(':', '').replace('T', '_')}",
            )
        except Exception as e:
            # Log error but don't fail the exit
            print(f"Warning: Failed to record trade to history: {e}")
    
    return result_position


def recalculate_unrealized_pnl(position: Position | dict) -> Position:
    current = _coerce_position(position)
    if current.status != "open":
        unrealized = 0.0
    else:
        unrealized = (
            current.current_price - current.entry_price
        ) * current.remaining_quantity

    return Position(
        **{
            **asdict(current),
            "unrealized_pnl_usdt": round(unrealized, 8),
        }
    )


def calculate_realized_pnl(
    entry_price: float,
    exit_price: float,
    quantity: float,
    fee_usdt: float = 0.0,
) -> float:
    gross = (exit_price - entry_price) * quantity
    return round(gross - max(0.0, fee_usdt), 8)


def weighted_average_price(
    current_price: float,
    current_quantity: float,
    added_price: float,
    added_quantity: float,
) -> float:
    total_quantity = current_quantity + added_quantity
    if total_quantity <= 0:
        return 0.0
    total_cost = current_price * current_quantity + added_price * added_quantity
    return round(total_cost / total_quantity, 8)


def require_position(snapshot: PositionSnapshot, symbol: str) -> Position:
    position = snapshot.positions.get(symbol)
    if position is None:
        raise KeyError(f"No position found for {symbol}")
    return position


def snapshot_from_state(state: dict) -> PositionSnapshot:
    def _extract_positions_map(raw_state: dict) -> dict:
        if not isinstance(raw_state, dict):
            return {}
        # Preferred structure: {'positions': {symbol: payload}}
        if "positions" in raw_state:
            pos = raw_state.get("positions") or {}
            if isinstance(pos, dict):
                return pos
            if isinstance(pos, list):
                result = {}
                for item in pos:
                    if isinstance(item, dict):
                        sym = item.get("symbol")
                        if sym:
                            result[str(sym)] = {k: v for k, v in item.items() if k != "symbol"}
                return result
            return {}
        # Legacy/alternate: top-level mapping of symbol -> payload
        return {k: v for k, v in raw_state.items() if isinstance(k, str) and isinstance(v, dict)}

    positions = {
        symbol: _coerce_position({"symbol": symbol, **payload})
        for symbol, payload in _extract_positions_map(state).items()
        if isinstance(payload, dict)
    }
    return PositionSnapshot(
        positions=positions,
        updated_at=state.get("updated_at"),
    )


def snapshot_to_state(snapshot: PositionSnapshot) -> dict:
    return {
        "positions": {
            symbol: _position_to_storage_dict(position)
            for symbol, position in snapshot.positions.items()
        },
        "updated_at": snapshot.updated_at or _now_iso(),
    }


def load_position_state_from_path(path: Path) -> dict:
    import json

    if not path.exists():
        return {"positions": {}, "updated_at": None}
    with path.open("r", encoding="utf-8") as file:
        raw = json.load(file)
    if not isinstance(raw, dict):
        return {"positions": {}, "updated_at": None}
    raw.setdefault("positions", {})
    raw.setdefault("updated_at", None)
    return raw


def save_position_state_to_path(state: dict, path: Path) -> None:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(state, file, indent=2)


def _position_to_storage_dict(position: Position) -> dict:
    payload = position.to_dict()
    payload.pop("hold_minutes", None)
    payload.pop("hold_hours", None)
    payload.pop("partial_exits_taken", None)
    payload.pop("symbol", None)
    return payload


def _coerce_position(position: Position | dict) -> Position:
    if isinstance(position, Position):
        return position

    events = position.get("events", [])
    parsed_events = [
        event if isinstance(event, PositionEvent) else PositionEvent(**event)
        for event in events
        if isinstance(event, (PositionEvent, dict))
    ]
    symbol = str(position.get("symbol", ""))
    entry_price = _safe_float(position.get("entry_price", position.get("avg", 0.0)))
    current_price = _safe_float(
        position.get("current_price", position.get("price", entry_price))
    )
    quantity = _safe_float(position.get("quantity", position.get("qty", 0.0)))
    remaining = _safe_float(position.get("remaining_quantity", quantity))
    opened_at = str(position.get("opened_at", position.get("entry_time", _now_iso())))
    tp1_hit = bool(position.get("tp1_hit", False))
    tp2_hit = bool(position.get("tp2_hit", False))
    partials = set(position.get("partial_exits_taken", []))
    if "tp1" in partials:
        tp1_hit = True
    if "tp2" in partials:
        tp2_hit = True

    coerced = Position(
        symbol=symbol,
        status=position.get("status", "open"),
        entry_price=entry_price,
        current_price=current_price,
        quantity=quantity,
        remaining_quantity=remaining,
        opened_at=opened_at,
        entry_type=str(position.get("entry_type", "unknown")),
        entry_grade=str(position.get("entry_grade", "unknown")),
        entry_score=int(position.get("entry_score", position.get("score", 0))),
        regime_at_entry=str(position.get("regime_at_entry", "unknown")),
        stop_loss_price=_optional_float(position.get("stop_loss_price")),
        trailing_stop_price=_optional_float(position.get("trailing_stop_price")),
        highest_price=_optional_float(position.get("highest_price")) or current_price,
        tp1_hit=tp1_hit,
        tp2_hit=tp2_hit,
        emergency_mode=bool(position.get("emergency_mode", False)),
        realized_pnl_usdt=_safe_float(position.get("realized_pnl_usdt", 0.0)),
        unrealized_pnl_usdt=_safe_float(position.get("unrealized_pnl_usdt", 0.0)),
        entry_fee_usdt=_safe_float(position.get("entry_fee_usdt", 0.0)),
        exit_fee_usdt=_safe_float(position.get("exit_fee_usdt", 0.0)),
        total_fees_usdt=_safe_float(position.get("total_fees_usdt", 0.0)),
        closed_at=position.get("closed_at"),
        exit_reason=position.get("exit_reason"),
        last_adjustment_at=position.get("last_adjustment_at"),
        events=parsed_events,
    )
    return recalculate_unrealized_pnl(coerced) if coerced.status == "open" else coerced


def _coerce_entry_decision(entry_decision: EntryDecision | dict | None) -> dict:
    if entry_decision is None:
        return {}
    if isinstance(entry_decision, EntryDecision):
        return entry_decision.to_dict()
    return dict(entry_decision)


def _coerce_exit_decision(exit_decision: ExitDecision | dict) -> dict:
    if isinstance(exit_decision, ExitDecision):
        return exit_decision.to_dict()
    return dict(exit_decision)


def _to_iso(value: datetime | str | None = None) -> str:
    parsed = _parse_datetime(value)
    if parsed is None:
        parsed = datetime.now(UTC)
    return parsed.isoformat()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_datetime(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _safe_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
