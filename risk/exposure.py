from dataclasses import asdict, dataclass, field
from typing import Literal

from config import MAX_OPEN_POSITIONS, MAX_PORTFOLIO_EXPOSURE
from risk.position_sizing import PositionSizeRecommendation


ExposureStatus = Literal["approved", "reduced", "blocked"]
CorrelationGroup = Literal["major_crypto", "other"]


@dataclass(frozen=True)
class ExposureConfig:
    max_portfolio_exposure_percent: float = MAX_PORTFOLIO_EXPOSURE * 100
    max_symbol_exposure_percent: float = 18.0
    max_correlated_group_exposure_percent: float = 30.0
    max_open_positions: int = MAX_OPEN_POSITIONS
    max_positions_per_symbol: int = 1
    max_daily_loss_percent: float = 4.0
    max_weekly_loss_percent: float = 8.0
    max_consecutive_losses: int = 4
    min_allowed_trade_usdt: float = 0.50
    major_crypto_symbols: tuple[str, ...] = ("BTC/USDT", "ETH/USDT")


@dataclass(frozen=True)
class OpenPosition:
    symbol: str
    exposure_usdt: float
    quantity: float = 0.0
    entry_price: float = 0.0


@dataclass(frozen=True)
class ExposureContext:
    wallet_total_usdt: float
    open_positions: list[OpenPosition | dict] | dict = field(default_factory=list)
    daily_loss_percent: float = 0.0
    weekly_loss_percent: float = 0.0
    consecutive_losses: int = 0
    emergency_mode: bool = False


@dataclass(frozen=True)
class ExposureDecision:
    status: ExposureStatus
    approved_usdt: float
    portfolio_exposure_after_percent: float
    symbol_exposure_after_percent: float
    correlated_exposure_after_percent: float
    open_positions_after: int
    reasons: list[str]
    warnings: list[str]
    blockers: list[str]

    @property
    def can_trade(self) -> bool:
        return self.status in {"approved", "reduced"} and not self.blockers

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["can_trade"] = self.can_trade
        return payload


def evaluate_exposure(
    symbol: str,
    proposed_size: PositionSizeRecommendation | dict | float,
    context: ExposureContext | dict,
    config: ExposureConfig | None = None,
) -> ExposureDecision:
    config = config or ExposureConfig()
    exposure_context = _coerce_context(context)
    requested_usdt = _coerce_size(proposed_size)
    open_positions = [_coerce_position(item) for item in exposure_context.open_positions]

    reasons: list[str] = []
    warnings: list[str] = []
    blockers = survival_rule_blockers(exposure_context, config=config)

    if exposure_context.wallet_total_usdt <= 0:
        blockers.append("Wallet total is unavailable")

    if requested_usdt <= 0:
        blockers.append("Proposed size is zero")

    if len(open_positions) >= config.max_open_positions:
        blockers.append("Maximum open positions reached")

    if symbol_position_count(symbol, open_positions) >= config.max_positions_per_symbol:
        blockers.append(f"Maximum position stack reached for {symbol}")

    if blockers:
        return _decision(
            status="blocked",
            approved_usdt=0.0,
            symbol=symbol,
            open_positions=open_positions,
            context=exposure_context,
            reasons=reasons,
            warnings=warnings,
            blockers=blockers,
            config=config,
        )

    portfolio_room = exposure_room_usdt(
        exposure_context.wallet_total_usdt,
        total_exposure_usdt(open_positions),
        config.max_portfolio_exposure_percent,
    )
    symbol_room = exposure_room_usdt(
        exposure_context.wallet_total_usdt,
        symbol_exposure_usdt(symbol, open_positions),
        config.max_symbol_exposure_percent,
    )
    correlated_room = exposure_room_usdt(
        exposure_context.wallet_total_usdt,
        correlated_group_exposure_usdt(symbol, open_positions, config=config),
        config.max_correlated_group_exposure_percent,
    )

    approved_usdt = min(requested_usdt, portfolio_room, symbol_room, correlated_room)

    if approved_usdt < requested_usdt:
        warnings.append("Size reduced by exposure limits")

    if approved_usdt < config.min_allowed_trade_usdt:
        blockers.append("Approved size is below minimum allowed trade")
        approved_usdt = 0.0

    if symbol in config.major_crypto_symbols and correlated_room < requested_usdt:
        warnings.append("BTC/ETH correlation cap reduced available exposure")

    reasons.extend(
        [
            f"Portfolio exposure room is {portfolio_room:.2f} USDT",
            f"Symbol exposure room is {symbol_room:.2f} USDT",
            f"Correlated group room is {correlated_room:.2f} USDT",
        ]
    )

    status: ExposureStatus = "approved"
    if blockers:
        status = "blocked"
    elif approved_usdt < requested_usdt:
        status = "reduced"

    return _decision(
        status=status,
        approved_usdt=approved_usdt,
        symbol=symbol,
        open_positions=open_positions,
        context=exposure_context,
        reasons=reasons,
        warnings=warnings,
        blockers=blockers,
        config=config,
    )


def survival_rule_blockers(
    context: ExposureContext | dict,
    config: ExposureConfig | None = None,
) -> list[str]:
    config = config or ExposureConfig()
    exposure_context = _coerce_context(context)
    blockers = []

    if exposure_context.emergency_mode:
        blockers.append("Emergency mode is active")

    if exposure_context.daily_loss_percent >= config.max_daily_loss_percent:
        blockers.append("Maximum daily loss reached")

    if exposure_context.weekly_loss_percent >= config.max_weekly_loss_percent:
        blockers.append("Maximum weekly loss reached")

    if exposure_context.consecutive_losses >= config.max_consecutive_losses:
        blockers.append("Maximum consecutive losses reached")

    return blockers


def total_exposure_usdt(open_positions: list[OpenPosition]) -> float:
    return sum(max(0.0, position.exposure_usdt) for position in open_positions)


def symbol_exposure_usdt(symbol: str, open_positions: list[OpenPosition]) -> float:
    return sum(
        max(0.0, position.exposure_usdt)
        for position in open_positions
        if position.symbol == symbol
    )


def correlated_group_exposure_usdt(
    symbol: str,
    open_positions: list[OpenPosition],
    config: ExposureConfig | None = None,
) -> float:
    config = config or ExposureConfig()
    group = correlation_group(symbol, config=config)
    return sum(
        max(0.0, position.exposure_usdt)
        for position in open_positions
        if correlation_group(position.symbol, config=config) == group
    )


def symbol_position_count(symbol: str, open_positions: list[OpenPosition]) -> int:
    return sum(1 for position in open_positions if position.symbol == symbol)


def correlation_group(
    symbol: str,
    config: ExposureConfig | None = None,
) -> CorrelationGroup:
    config = config or ExposureConfig()
    if symbol in config.major_crypto_symbols:
        return "major_crypto"
    return "other"


def exposure_room_usdt(
    wallet_total_usdt: float,
    current_exposure_usdt: float,
    limit_percent: float,
) -> float:
    limit_usdt = wallet_total_usdt * limit_percent / 100
    return max(0.0, limit_usdt - current_exposure_usdt)


def _decision(
    status: ExposureStatus,
    approved_usdt: float,
    symbol: str,
    open_positions: list[OpenPosition],
    context: ExposureContext,
    reasons: list[str],
    warnings: list[str],
    blockers: list[str],
    config: ExposureConfig,
) -> ExposureDecision:
    total_after = total_exposure_usdt(open_positions) + approved_usdt
    symbol_after = symbol_exposure_usdt(symbol, open_positions) + approved_usdt
    correlated_after = (
        correlated_group_exposure_usdt(symbol, open_positions, config=config)
        + approved_usdt
    )
    open_positions_after = len(open_positions) + (1 if approved_usdt > 0 else 0)

    return ExposureDecision(
        status=status,
        approved_usdt=round(approved_usdt, 4),
        portfolio_exposure_after_percent=_percent(total_after, context.wallet_total_usdt),
        symbol_exposure_after_percent=_percent(symbol_after, context.wallet_total_usdt),
        correlated_exposure_after_percent=_percent(correlated_after, context.wallet_total_usdt),
        open_positions_after=open_positions_after,
        reasons=_dedupe(reasons),
        warnings=_dedupe(warnings),
        blockers=_dedupe(blockers),
    )


def _coerce_size(proposed_size: PositionSizeRecommendation | dict | float) -> float:
    if isinstance(proposed_size, PositionSizeRecommendation):
        return proposed_size.recommended_usdt
    if isinstance(proposed_size, dict):
        return float(proposed_size.get("recommended_usdt", 0.0))
    return float(proposed_size)


def _coerce_context(context: ExposureContext | dict) -> ExposureContext:
    if isinstance(context, ExposureContext):
        return context

    return ExposureContext(
        wallet_total_usdt=float(context.get("wallet_total_usdt", 0.0)),
        open_positions=_normalize_open_positions(
            context.get("open_positions", context.get("positions", []))
        ),
        daily_loss_percent=float(context.get("daily_loss_percent", 0.0)),
        weekly_loss_percent=float(context.get("weekly_loss_percent", 0.0)),
        consecutive_losses=int(context.get("consecutive_losses", 0)),
        emergency_mode=bool(context.get("emergency_mode", False)),
    )


def _normalize_open_positions(raw_positions: object) -> list[OpenPosition | dict]:
    if raw_positions is None:
        return []

    if isinstance(raw_positions, list):
        return raw_positions

    if isinstance(raw_positions, tuple):
        return list(raw_positions)

    if isinstance(raw_positions, dict):
        if "symbol" in raw_positions:
            return [raw_positions]

        normalized = []
        for symbol, payload in raw_positions.items():
            if isinstance(payload, dict):
                item = dict(payload)
                item.setdefault("symbol", symbol)
                normalized.append(item)
            else:
                normalized.append(
                    {
                        "symbol": symbol,
                        "exposure_usdt": payload,
                    }
                )
        return normalized

    return []


def _coerce_position(position: OpenPosition | dict | object) -> OpenPosition:
    if isinstance(position, OpenPosition):
        return position

    if not isinstance(position, dict):
        return OpenPosition(symbol=str(position), exposure_usdt=0.0)

    quantity = _safe_float(position.get("quantity", position.get("qty", 0.0)))
    entry_price = _safe_float(
        position.get("entry_price", position.get("avg", position.get("price", 0.0)))
    )
    exposure_usdt = position.get("exposure_usdt")
    if exposure_usdt is None:
        exposure_usdt = abs(quantity * entry_price)

    return OpenPosition(
        symbol=str(position.get("symbol", "")),
        exposure_usdt=_safe_float(exposure_usdt),
        quantity=quantity,
        entry_price=entry_price,
    )


def _percent(value: float, total: float) -> float:
    if total <= 0:
        return 0.0
    return round(value / total * 100, 4)


def _safe_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    deduped = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped
