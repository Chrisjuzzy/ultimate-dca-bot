from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from math import sqrt
from statistics import mean, pstdev
from typing import Iterable

from portfolio.positions import Position, PositionSnapshot, snapshot_from_state


@dataclass(frozen=True)
class TradeRecord:
    symbol: str
    opened_at: str | None
    closed_at: str | None
    entry_type: str
    entry_grade: str
    entry_score: int
    regime: str
    exit_reason: str
    realized_pnl_usdt: float
    total_fees_usdt: float
    gross_pnl_usdt: float
    quantity: float
    entry_price: float
    exit_price: float | None
    hold_hours: float
    event_count: int

    @property
    def is_win(self) -> bool:
        return self.realized_pnl_usdt > 0

    @property
    def is_loss(self) -> bool:
        return self.realized_pnl_usdt < 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class EquityPoint:
    timestamp: str
    equity_usdt: float
    realized_pnl_usdt: float
    drawdown_percent: float
    symbol: str
    event_type: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class GroupPerformance:
    group: str
    trades: int
    wins: int
    losses: int
    win_rate_percent: float
    net_pnl_usdt: float
    average_pnl_usdt: float
    profit_factor: float
    expectancy_usdt: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class PerformanceReport:
    starting_equity_usdt: float
    ending_equity_usdt: float
    net_pnl_usdt: float
    realized_pnl_usdt: float
    unrealized_pnl_usdt: float
    total_fees_usdt: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    breakeven_trades: int
    win_rate_percent: float
    average_win_usdt: float
    average_loss_usdt: float
    average_trade_usdt: float
    largest_win_usdt: float
    largest_loss_usdt: float
    profit_factor: float
    expectancy_usdt: float
    max_drawdown_percent: float
    recovery_factor: float
    sharpe_ratio: float
    average_hold_hours: float
    best_symbol: str | None
    worst_symbol: str | None
    best_entry_type: str | None
    worst_entry_type: str | None
    best_regime: str | None
    worst_regime: str | None
    trades: list[TradeRecord] = field(default_factory=list)
    equity_curve: list[EquityPoint] = field(default_factory=list)
    by_symbol: list[GroupPerformance] = field(default_factory=list)
    by_entry_type: list[GroupPerformance] = field(default_factory=list)
    by_regime: list[GroupPerformance] = field(default_factory=list)
    by_exit_reason: list[GroupPerformance] = field(default_factory=list)

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["trades"] = [trade.to_dict() for trade in self.trades]
        payload["equity_curve"] = [point.to_dict() for point in self.equity_curve]
        payload["by_symbol"] = [item.to_dict() for item in self.by_symbol]
        payload["by_entry_type"] = [item.to_dict() for item in self.by_entry_type]
        payload["by_regime"] = [item.to_dict() for item in self.by_regime]
        payload["by_exit_reason"] = [item.to_dict() for item in self.by_exit_reason]
        return payload


def build_performance_report(
    source: PositionSnapshot | dict | Iterable[Position | dict] | Iterable[TradeRecord | dict],
    starting_equity_usdt: float = 0.0,
    include_open_unrealized: bool = True,
) -> PerformanceReport:
    snapshot = coerce_snapshot(source)
    trades = extract_trade_records(snapshot)
    open_unrealized = (
        snapshot.total_unrealized_pnl_usdt if include_open_unrealized else 0.0
    )
    realized_pnl = round(sum(trade.realized_pnl_usdt for trade in trades), 8)
    total_fees = round(sum(trade.total_fees_usdt for trade in trades), 8)
    net_pnl = round(realized_pnl + open_unrealized, 8)
    ending_equity = round(starting_equity_usdt + net_pnl, 8)
    pnls = [trade.realized_pnl_usdt for trade in trades]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    breakevens = [pnl for pnl in pnls if pnl == 0]
    equity_curve = build_equity_curve(trades, starting_equity_usdt)
    max_drawdown = max_drawdown_percent(equity_curve)
    group_symbol = group_performance(trades, "symbol")
    group_entry = group_performance(trades, "entry_type")
    group_regime = group_performance(trades, "regime")
    group_exit = group_performance(trades, "exit_reason")

    return PerformanceReport(
        starting_equity_usdt=round(starting_equity_usdt, 8),
        ending_equity_usdt=ending_equity,
        net_pnl_usdt=net_pnl,
        realized_pnl_usdt=realized_pnl,
        unrealized_pnl_usdt=round(open_unrealized, 8),
        total_fees_usdt=total_fees,
        total_trades=len(trades),
        winning_trades=len(wins),
        losing_trades=len(losses),
        breakeven_trades=len(breakevens),
        win_rate_percent=round(_percent(len(wins), len(trades)), 4),
        average_win_usdt=round(mean(wins), 8) if wins else 0.0,
        average_loss_usdt=round(mean(losses), 8) if losses else 0.0,
        average_trade_usdt=round(mean(pnls), 8) if pnls else 0.0,
        largest_win_usdt=round(max(wins), 8) if wins else 0.0,
        largest_loss_usdt=round(min(losses), 8) if losses else 0.0,
        profit_factor=round(profit_factor(pnls), 8),
        expectancy_usdt=round(expectancy(pnls), 8),
        max_drawdown_percent=round(max_drawdown, 4),
        recovery_factor=round(recovery_factor(net_pnl, max_drawdown, starting_equity_usdt), 8),
        sharpe_ratio=round(sharpe_ratio(pnls), 8),
        average_hold_hours=round(
            mean([trade.hold_hours for trade in trades]), 4
        ) if trades else 0.0,
        best_symbol=best_group_name(group_symbol),
        worst_symbol=worst_group_name(group_symbol),
        best_entry_type=best_group_name(group_entry),
        worst_entry_type=worst_group_name(group_entry),
        best_regime=best_group_name(group_regime),
        worst_regime=worst_group_name(group_regime),
        trades=trades,
        equity_curve=equity_curve,
        by_symbol=group_symbol,
        by_entry_type=group_entry,
        by_regime=group_regime,
        by_exit_reason=group_exit,
    )


def extract_trade_records(snapshot: PositionSnapshot | dict) -> list[TradeRecord]:
    position_snapshot = coerce_snapshot(snapshot)
    records = []
    for position in position_snapshot.positions.values():
        if position.status != "closed":
            continue
        records.append(trade_record_from_position(position))
    return sorted(records, key=lambda trade: trade.closed_at or trade.opened_at or "")


def trade_record_from_position(position: Position | dict) -> TradeRecord:
    position_state = coerce_position(position)
    exit_price = last_exit_price(position_state)
    gross_pnl = position_state.realized_pnl_usdt + position_state.exit_fee_usdt

    return TradeRecord(
        symbol=position_state.symbol,
        opened_at=position_state.opened_at,
        closed_at=position_state.closed_at,
        entry_type=position_state.entry_type,
        entry_grade=position_state.entry_grade,
        entry_score=position_state.entry_score,
        regime=position_state.regime_at_entry,
        exit_reason=position_state.exit_reason or "unknown",
        realized_pnl_usdt=round(position_state.realized_pnl_usdt, 8),
        total_fees_usdt=round(position_state.total_fees_usdt, 8),
        gross_pnl_usdt=round(gross_pnl, 8),
        quantity=round(position_state.quantity, 8),
        entry_price=round(position_state.entry_price, 8),
        exit_price=round(exit_price, 8) if exit_price is not None else None,
        hold_hours=round(position_state.hold_hours, 4),
        event_count=len(position_state.events),
    )


def build_equity_curve(
    trades: Iterable[TradeRecord | dict],
    starting_equity_usdt: float = 0.0,
) -> list[EquityPoint]:
    trade_records = [coerce_trade_record(trade) for trade in trades]
    trade_records = sorted(
        trade_records,
        key=lambda trade: trade.closed_at or trade.opened_at or "",
    )
    equity = starting_equity_usdt
    peak = starting_equity_usdt
    curve = []

    for trade in trade_records:
        equity += trade.realized_pnl_usdt
        peak = max(peak, equity)
        drawdown = 0.0 if peak <= 0 else max(0.0, (peak - equity) / peak * 100)
        curve.append(
            EquityPoint(
                timestamp=trade.closed_at or trade.opened_at or "",
                equity_usdt=round(equity, 8),
                realized_pnl_usdt=round(trade.realized_pnl_usdt, 8),
                drawdown_percent=round(drawdown, 4),
                symbol=trade.symbol,
                event_type="trade_closed",
            )
        )

    return curve


def group_performance(
    trades: Iterable[TradeRecord | dict],
    field_name: str,
) -> list[GroupPerformance]:
    grouped: dict[str, list[TradeRecord]] = {}
    for trade in trades:
        record = coerce_trade_record(trade)
        group = str(getattr(record, field_name, "unknown") or "unknown")
        grouped.setdefault(group, []).append(record)

    reports = []
    for group, records in grouped.items():
        pnls = [record.realized_pnl_usdt for record in records]
        wins = [pnl for pnl in pnls if pnl > 0]
        losses = [pnl for pnl in pnls if pnl < 0]
        reports.append(
            GroupPerformance(
                group=group,
                trades=len(records),
                wins=len(wins),
                losses=len(losses),
                win_rate_percent=round(_percent(len(wins), len(records)), 4),
                net_pnl_usdt=round(sum(pnls), 8),
                average_pnl_usdt=round(mean(pnls), 8) if pnls else 0.0,
                profit_factor=round(profit_factor(pnls), 8),
                expectancy_usdt=round(expectancy(pnls), 8),
            )
        )

    return sorted(reports, key=lambda item: item.net_pnl_usdt, reverse=True)


def profit_factor(pnls: Iterable[float]) -> float:
    values = [float(pnl) for pnl in pnls]
    gross_profit = sum(pnl for pnl in values if pnl > 0)
    gross_loss = abs(sum(pnl for pnl in values if pnl < 0))
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def expectancy(pnls: Iterable[float]) -> float:
    values = [float(pnl) for pnl in pnls]
    if not values:
        return 0.0
    return mean(values)


def sharpe_ratio(pnls: Iterable[float]) -> float:
    values = [float(pnl) for pnl in pnls]
    if len(values) < 2:
        return 0.0
    deviation = pstdev(values)
    if deviation == 0:
        return 0.0
    return mean(values) / deviation * sqrt(len(values))


def max_drawdown_percent(equity_curve: Iterable[EquityPoint | dict]) -> float:
    points = [coerce_equity_point(point) for point in equity_curve]
    if not points:
        return 0.0
    return max(point.drawdown_percent for point in points)


def recovery_factor(
    net_pnl_usdt: float,
    max_drawdown_pct: float,
    starting_equity_usdt: float,
) -> float:
    if starting_equity_usdt <= 0 or max_drawdown_pct <= 0:
        return 0.0
    drawdown_usdt = starting_equity_usdt * max_drawdown_pct / 100
    if drawdown_usdt <= 0:
        return 0.0
    return net_pnl_usdt / drawdown_usdt


def best_group_name(groups: list[GroupPerformance]) -> str | None:
    if not groups:
        return None
    return max(groups, key=lambda item: item.net_pnl_usdt).group


def worst_group_name(groups: list[GroupPerformance]) -> str | None:
    if not groups:
        return None
    return min(groups, key=lambda item: item.net_pnl_usdt).group


def last_exit_price(position: Position) -> float | None:
    for event in reversed(position.events):
        if event.event_type in {"partial_exited", "closed"} and event.price > 0:
            return event.price
    return position.current_price if position.current_price > 0 else None


def coerce_snapshot(
    source: PositionSnapshot | dict | Iterable[Position | dict] | Iterable[TradeRecord | dict],
) -> PositionSnapshot:
    if isinstance(source, PositionSnapshot):
        return source

    if isinstance(source, dict):
        if "positions" in source:
            return snapshot_from_state(source)
        if all(isinstance(value, dict) for value in source.values()):
            return snapshot_from_state({"positions": source, "updated_at": None})

    if isinstance(source, Iterable) and not isinstance(source, (str, bytes, dict)):
        positions: dict[str, Position] = {}
        for index, item in enumerate(source):
            if isinstance(item, TradeRecord):
                position = position_from_trade_record(item)
            elif isinstance(item, dict) and "realized_pnl_usdt" in item and "entry_price" in item:
                position = position_from_trade_record(coerce_trade_record(item))
            else:
                position = coerce_position(item)
            positions[f"{position.symbol}#{index}"] = position
        return PositionSnapshot(positions=positions, updated_at=None)

    return PositionSnapshot(positions={}, updated_at=None)


def position_from_trade_record(trade: TradeRecord) -> Position:
    from portfolio.positions import PositionEvent

    closed_at = trade.closed_at or trade.opened_at or _now_iso()
    exit_price = trade.exit_price or trade.entry_price
    event = PositionEvent(
        event_type="closed",
        timestamp=closed_at,
        quantity=trade.quantity,
        price=exit_price,
        fee_usdt=trade.total_fees_usdt,
        realized_pnl_usdt=trade.realized_pnl_usdt,
        reason=trade.exit_reason,
    )
    return Position(
        symbol=trade.symbol,
        status="closed",
        entry_price=trade.entry_price,
        current_price=exit_price,
        quantity=trade.quantity,
        remaining_quantity=0.0,
        opened_at=trade.opened_at or closed_at,
        entry_type=trade.entry_type,
        entry_grade=trade.entry_grade,
        entry_score=trade.entry_score,
        regime_at_entry=trade.regime,
        realized_pnl_usdt=trade.realized_pnl_usdt,
        unrealized_pnl_usdt=0.0,
        total_fees_usdt=trade.total_fees_usdt,
        closed_at=closed_at,
        exit_reason=trade.exit_reason,
        events=[event],
    )


def coerce_position(position: Position | dict) -> Position:
    if isinstance(position, Position):
        return position
    return snapshot_from_state(
        {"positions": {position.get("symbol", "UNKNOWN"): position}, "updated_at": None}
    ).positions.get(position.get("symbol", "UNKNOWN"))


def coerce_trade_record(trade: TradeRecord | dict) -> TradeRecord:
    if isinstance(trade, TradeRecord):
        return trade
    return TradeRecord(
        symbol=str(trade.get("symbol", "")),
        opened_at=trade.get("opened_at"),
        closed_at=trade.get("closed_at"),
        entry_type=str(trade.get("entry_type", "unknown")),
        entry_grade=str(trade.get("entry_grade", "unknown")),
        entry_score=int(trade.get("entry_score", 0)),
        regime=str(trade.get("regime", trade.get("regime_at_entry", "unknown"))),
        exit_reason=str(trade.get("exit_reason", "unknown")),
        realized_pnl_usdt=_safe_float(trade.get("realized_pnl_usdt", 0.0)),
        total_fees_usdt=_safe_float(trade.get("total_fees_usdt", 0.0)),
        gross_pnl_usdt=_safe_float(
            trade.get("gross_pnl_usdt", trade.get("realized_pnl_usdt", 0.0))
        ),
        quantity=_safe_float(trade.get("quantity", 0.0)),
        entry_price=_safe_float(trade.get("entry_price", 0.0)),
        exit_price=_optional_float(trade.get("exit_price")),
        hold_hours=_safe_float(trade.get("hold_hours", 0.0)),
        event_count=int(trade.get("event_count", 0)),
    )


def coerce_equity_point(point: EquityPoint | dict) -> EquityPoint:
    if isinstance(point, EquityPoint):
        return point
    return EquityPoint(
        timestamp=str(point.get("timestamp", "")),
        equity_usdt=_safe_float(point.get("equity_usdt", 0.0)),
        realized_pnl_usdt=_safe_float(point.get("realized_pnl_usdt", 0.0)),
        drawdown_percent=_safe_float(point.get("drawdown_percent", 0.0)),
        symbol=str(point.get("symbol", "")),
        event_type=str(point.get("event_type", "")),
    )


def _percent(part: float, total: float) -> float:
    if total <= 0:
        return 0.0
    return part / total * 100


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


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
