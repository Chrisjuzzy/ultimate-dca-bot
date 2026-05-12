from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from time import monotonic, sleep
from typing import Literal
from uuid import uuid4


OrderSide = Literal["buy", "sell"]
OrderType = Literal["market", "limit"]
OrderStatus = Literal[
    "validated",
    "submitted",
    "filled",
    "partially_filled",
    "open",
    "blocked",
    "failed",
]


@dataclass(frozen=True)
class OrderManagerConfig:
    max_retries: int = 3
    retry_delay_seconds: float = 2.0
    lock_ttl_seconds: int = 60
    verification_attempts: int = 3
    verification_delay_seconds: float = 2.0
    max_slippage_percent: float = 0.35
    default_min_notional_usdt: float = 5.0
    client_order_prefix: str = "udb"
    allow_market_orders: bool = True
    allow_limit_orders: bool = True
    dry_run: bool = False


@dataclass(frozen=True)
class OrderRequest:
    symbol: str
    side: OrderSide
    order_type: OrderType = "market"
    amount: float | None = None
    quote_amount_usdt: float | None = None
    price: float | None = None
    expected_price: float | None = None
    max_slippage_percent: float | None = None
    client_order_id: str | None = None
    idempotency_key: str | None = None
    reduce_only: bool = False
    dry_run: bool = False
    params: dict = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedOrder:
    symbol: str
    side: OrderSide
    order_type: OrderType
    amount: float
    price: float | None
    expected_price: float | None
    client_order_id: str
    idempotency_key: str
    params: dict
    notional_usdt: float
    max_slippage_percent: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class OrderValidation:
    valid: bool
    normalized_order: NormalizedOrder | None
    reasons: list[str]
    warnings: list[str]
    blockers: list[str]

    def to_dict(self) -> dict:
        payload = asdict(self)
        if self.normalized_order is not None:
            payload["normalized_order"] = self.normalized_order.to_dict()
        return payload


@dataclass(frozen=True)
class OrderResult:
    status: OrderStatus
    symbol: str
    side: OrderSide
    order_type: OrderType
    requested_amount: float
    submitted_amount: float
    filled_amount: float
    remaining_amount: float
    requested_price: float | None
    average_fill_price: float | None
    expected_price: float | None
    slippage_percent: float | None
    order_id: str | None
    client_order_id: str | None
    idempotency_key: str | None
    attempts: int
    latency_ms: int
    raw_order: dict | None
    reasons: list[str]
    warnings: list[str]
    blockers: list[str]

    @property
    def success(self) -> bool:
        return self.status in {"filled", "partially_filled", "open", "validated"}

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["success"] = self.success
        return payload


class ExecutionLockBook:
    def __init__(self) -> None:
        self._locks: dict[str, datetime] = {}

    def acquire(self, symbol: str, ttl_seconds: int) -> bool:
        self.release_expired()
        if self.is_locked(symbol):
            return False
        self._locks[symbol] = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
        return True

    def release(self, symbol: str) -> None:
        self._locks.pop(symbol, None)

    def is_locked(self, symbol: str) -> bool:
        expires_at = self._locks.get(symbol)
        if expires_at is None:
            return False
        if expires_at <= datetime.now(UTC):
            self.release(symbol)
            return False
        return True

    def release_expired(self) -> None:
        now = datetime.now(UTC)
        expired = [symbol for symbol, expires_at in self._locks.items() if expires_at <= now]
        for symbol in expired:
            self.release(symbol)


class OrderLedger:
    def __init__(self) -> None:
        self._orders: dict[str, OrderResult] = {}

    def has(self, idempotency_key: str) -> bool:
        return idempotency_key in self._orders

    def get(self, idempotency_key: str) -> OrderResult | None:
        return self._orders.get(idempotency_key)

    def record(self, idempotency_key: str, result: OrderResult) -> None:
        self._orders[idempotency_key] = result


class SafeOrderManager:
    def __init__(
        self,
        exchange,
        config: OrderManagerConfig | None = None,
        lock_book: ExecutionLockBook | None = None,
        ledger: OrderLedger | None = None,
    ) -> None:
        self.exchange = exchange
        self.config = config or OrderManagerConfig()
        self.lock_book = lock_book or ExecutionLockBook()
        self.ledger = ledger or OrderLedger()

    def validate_order(self, request: OrderRequest | dict) -> OrderValidation:
        return validate_order_request(self.exchange, request, config=self.config)

    def place_order(self, request: OrderRequest | dict) -> OrderResult:
        order_request = _coerce_request(request)
        validation = self.validate_order(order_request)

        if not validation.valid or validation.normalized_order is None:
            return _result_from_validation(
                request=order_request,
                validation=validation,
                status="blocked",
            )

        normalized = validation.normalized_order

        if self.ledger.has(normalized.idempotency_key):
            previous = self.ledger.get(normalized.idempotency_key)
            return _clone_with_warning(
                previous,
                "Duplicate idempotency key blocked; returning previous order result",
            )

        if not self.lock_book.acquire(normalized.symbol, self.config.lock_ttl_seconds):
            return _blocked_result(
                request=order_request,
                normalized=normalized,
                blockers=[f"Execution lock is active for {normalized.symbol}"],
            )

        try:
            if self.config.dry_run or order_request.dry_run:
                result = _validated_result(
                    request=order_request,
                    normalized=normalized,
                    reasons=validation.reasons + ["Dry run enabled; order not submitted"],
                    warnings=validation.warnings,
                )
                self.ledger.record(normalized.idempotency_key, result)
                return result

            result = submit_order_with_retries(
                exchange=self.exchange,
                normalized=normalized,
                config=self.config,
                validation=validation,
            )
            self.ledger.record(normalized.idempotency_key, result)
            return result
        finally:
            self.lock_book.release(normalized.symbol)


def validate_order_request(
    exchange,
    request: OrderRequest | dict,
    config: OrderManagerConfig | None = None,
) -> OrderValidation:
    config = config or OrderManagerConfig()
    order_request = _coerce_request(request)
    reasons: list[str] = []
    warnings: list[str] = []
    blockers: list[str] = []

    if not order_request.symbol:
        blockers.append("Symbol is required")

    if order_request.side not in {"buy", "sell"}:
        blockers.append("Order side must be buy or sell")

    if order_request.order_type == "market" and not config.allow_market_orders:
        blockers.append("Market orders are disabled")

    if order_request.order_type == "limit" and not config.allow_limit_orders:
        blockers.append("Limit orders are disabled")

    if order_request.order_type == "limit" and not order_request.price:
        blockers.append("Limit orders require a price")

    market = get_market(exchange, order_request.symbol)
    if market is None and order_request.symbol:
        blockers.append(f"Market metadata unavailable for {order_request.symbol}")

    reference_price = resolve_reference_price(order_request)
    amount = resolve_order_amount(order_request, reference_price)
    if amount <= 0:
        blockers.append("Order amount is zero")

    if reference_price is None or reference_price <= 0:
        warnings.append("Reference price unavailable; notional and slippage checks are limited")

    normalized_amount = normalize_amount(exchange, order_request.symbol, amount)
    normalized_price = normalize_price(exchange, order_request.symbol, order_request.price)
    expected_price = order_request.expected_price or normalized_price or reference_price
    notional = estimate_notional_usdt(
        amount=normalized_amount,
        price=expected_price or reference_price,
        quote_amount_usdt=order_request.quote_amount_usdt,
    )

    if market is not None:
        min_amount = market_min_amount(market)
        if min_amount is not None and normalized_amount < min_amount:
            blockers.append(
                f"Amount {normalized_amount} is below exchange minimum {min_amount}"
            )

        min_notional = market_min_notional(market) or config.default_min_notional_usdt
    else:
        min_notional = config.default_min_notional_usdt

    if notional < min_notional:
        blockers.append(f"Notional {notional:.4f} USDT is below minimum {min_notional:.4f}")

    if normalized_amount != amount:
        reasons.append(f"Amount normalized from {amount} to {normalized_amount}")

    if normalized_price != order_request.price and order_request.price is not None:
        reasons.append(f"Price normalized from {order_request.price} to {normalized_price}")

    client_order_id = order_request.client_order_id or build_client_order_id(
        prefix=config.client_order_prefix,
        symbol=order_request.symbol,
        side=order_request.side,
    )
    idempotency_key = order_request.idempotency_key or client_order_id
    params = dict(order_request.params)
    params.setdefault("newClientOrderId", client_order_id)

    normalized = None
    if not blockers:
        normalized = NormalizedOrder(
            symbol=order_request.symbol,
            side=order_request.side,
            order_type=order_request.order_type,
            amount=normalized_amount,
            price=normalized_price,
            expected_price=expected_price,
            client_order_id=client_order_id,
            idempotency_key=idempotency_key,
            params=params,
            notional_usdt=round(notional, 8),
            max_slippage_percent=(
                order_request.max_slippage_percent
                if order_request.max_slippage_percent is not None
                else config.max_slippage_percent
            ),
        )
        reasons.append("Order request passed pre-flight validation")

    return OrderValidation(
        valid=not blockers,
        normalized_order=normalized,
        reasons=_dedupe(reasons),
        warnings=_dedupe(warnings),
        blockers=_dedupe(blockers),
    )


def submit_order_with_retries(
    exchange,
    normalized: NormalizedOrder,
    config: OrderManagerConfig,
    validation: OrderValidation,
) -> OrderResult:
    attempts = 0
    warnings = list(validation.warnings)
    reasons = list(validation.reasons)
    started = monotonic()
    last_error: Exception | None = None

    while attempts < max(1, config.max_retries):
        attempts += 1
        try:
            submitted = create_order(exchange, normalized)
            verified = verify_order_fill(
                exchange=exchange,
                submitted_order=submitted,
                normalized=normalized,
                config=config,
            )
            result = build_order_result(
                normalized=normalized,
                raw_order=verified,
                attempts=attempts,
                latency_ms=_latency_ms(started),
                reasons=reasons + ["Order submitted and verification completed"],
                warnings=warnings,
            )

            slippage_warning = slippage_warning_for_result(result, normalized)
            if slippage_warning:
                warnings.append(slippage_warning)
                result = build_order_result(
                    normalized=normalized,
                    raw_order=verified,
                    attempts=attempts,
                    latency_ms=_latency_ms(started),
                    reasons=reasons + ["Order submitted and verification completed"],
                    warnings=warnings,
                )

            return result
        except Exception as exc:
            last_error = exc
            warnings.append(f"Attempt {attempts} failed: {exc}")
            if attempts < config.max_retries:
                sleep(config.retry_delay_seconds)

    return OrderResult(
        status="failed",
        symbol=normalized.symbol,
        side=normalized.side,
        order_type=normalized.order_type,
        requested_amount=normalized.amount,
        submitted_amount=0.0,
        filled_amount=0.0,
        remaining_amount=normalized.amount,
        requested_price=normalized.price,
        average_fill_price=None,
        expected_price=normalized.expected_price,
        slippage_percent=None,
        order_id=None,
        client_order_id=normalized.client_order_id,
        idempotency_key=normalized.idempotency_key,
        attempts=attempts,
        latency_ms=_latency_ms(started),
        raw_order=None,
        reasons=_dedupe(reasons),
        warnings=_dedupe(warnings),
        blockers=[f"Order failed after retries: {last_error}"],
    )


def create_order(exchange, normalized: NormalizedOrder) -> dict:
    if normalized.order_type == "market":
        return exchange.create_order(
            normalized.symbol,
            "market",
            normalized.side,
            normalized.amount,
            None,
            normalized.params,
        )

    return exchange.create_order(
        normalized.symbol,
        "limit",
        normalized.side,
        normalized.amount,
        normalized.price,
        normalized.params,
    )


def verify_order_fill(
    exchange,
    submitted_order: dict,
    normalized: NormalizedOrder,
    config: OrderManagerConfig,
) -> dict:
    order_id = str(submitted_order.get("id") or "")
    if not order_id or not hasattr(exchange, "fetch_order"):
        return submitted_order

    latest = submitted_order
    for _ in range(max(1, config.verification_attempts)):
        try:
            latest = exchange.fetch_order(order_id, normalized.symbol)
            if order_status(latest) in {"closed", "canceled", "rejected", "expired"}:
                return latest
            if filled_amount(latest) >= normalized.amount:
                return latest
        except Exception:
            pass
        sleep(config.verification_delay_seconds)

    return latest


def build_order_result(
    normalized: NormalizedOrder,
    raw_order: dict,
    attempts: int,
    latency_ms: int,
    reasons: list[str],
    warnings: list[str],
) -> OrderResult:
    filled = filled_amount(raw_order)
    submitted_amount = safe_float(raw_order.get("amount"), normalized.amount)
    remaining = max(0.0, safe_float(raw_order.get("remaining"), submitted_amount - filled))
    average = average_fill_price(raw_order)
    status = classify_order_status(raw_order, filled=filled, amount=submitted_amount)
    slippage = calculate_slippage_percent(
        expected_price=normalized.expected_price,
        average_fill_price=average,
        side=normalized.side,
    )

    return OrderResult(
        status=status,
        symbol=normalized.symbol,
        side=normalized.side,
        order_type=normalized.order_type,
        requested_amount=normalized.amount,
        submitted_amount=submitted_amount,
        filled_amount=filled,
        remaining_amount=remaining,
        requested_price=normalized.price,
        average_fill_price=average,
        expected_price=normalized.expected_price,
        slippage_percent=slippage,
        order_id=str(raw_order.get("id")) if raw_order.get("id") is not None else None,
        client_order_id=normalized.client_order_id,
        idempotency_key=normalized.idempotency_key,
        attempts=attempts,
        latency_ms=latency_ms,
        raw_order=raw_order,
        reasons=_dedupe(reasons),
        warnings=_dedupe(warnings),
        blockers=[],
    )


def classify_order_status(raw_order: dict, filled: float, amount: float) -> OrderStatus:
    status = order_status(raw_order)
    if status == "closed" or (amount > 0 and filled >= amount):
        return "filled"
    if filled > 0:
        return "partially_filled"
    if status == "open":
        return "open"
    if status in {"canceled", "rejected", "expired"}:
        return "failed"
    return "submitted"


def slippage_warning_for_result(
    result: OrderResult,
    normalized: NormalizedOrder,
) -> str | None:
    if result.slippage_percent is None:
        return None
    if result.slippage_percent > normalized.max_slippage_percent:
        return (
            f"Slippage {result.slippage_percent:.4f}% exceeded limit "
            f"{normalized.max_slippage_percent:.4f}%"
        )
    return None


def calculate_slippage_percent(
    expected_price: float | None,
    average_fill_price: float | None,
    side: OrderSide,
) -> float | None:
    if expected_price is None or expected_price <= 0 or average_fill_price is None:
        return None
    if side == "buy":
        return max(0.0, (average_fill_price - expected_price) / expected_price * 100)
    return max(0.0, (expected_price - average_fill_price) / expected_price * 100)


def get_market(exchange, symbol: str) -> dict | None:
    markets = getattr(exchange, "markets", None)
    if not markets and hasattr(exchange, "load_markets"):
        try:
            markets = exchange.load_markets()
        except Exception:
            markets = None
    if isinstance(markets, dict):
        return markets.get(symbol)
    return None


def normalize_amount(exchange, symbol: str, amount: float) -> float:
    if hasattr(exchange, "amount_to_precision"):
        try:
            return float(exchange.amount_to_precision(symbol, amount))
        except Exception:
            pass
    return round(max(0.0, amount), 8)


def normalize_price(exchange, symbol: str, price: float | None) -> float | None:
    if price is None:
        return None
    if hasattr(exchange, "price_to_precision"):
        try:
            return float(exchange.price_to_precision(symbol, price))
        except Exception:
            pass
    return round(max(0.0, price), 8)


def resolve_order_amount(request: OrderRequest, reference_price: float | None) -> float:
    if request.amount is not None:
        return max(0.0, request.amount)
    if (
        request.quote_amount_usdt is not None
        and reference_price is not None
        and reference_price > 0
    ):
        return max(0.0, request.quote_amount_usdt / reference_price)
    return 0.0


def resolve_reference_price(request: OrderRequest) -> float | None:
    return request.expected_price or request.price


def estimate_notional_usdt(
    amount: float,
    price: float | None,
    quote_amount_usdt: float | None = None,
) -> float:
    if quote_amount_usdt is not None:
        return max(0.0, quote_amount_usdt)
    if price is None:
        return 0.0
    return max(0.0, amount * price)


def market_min_amount(market: dict) -> float | None:
    limits = market.get("limits", {}) if isinstance(market, dict) else {}
    amount_limits = limits.get("amount", {})
    return optional_float(amount_limits.get("min"))


def market_min_notional(market: dict) -> float | None:
    limits = market.get("limits", {}) if isinstance(market, dict) else {}
    cost_limits = limits.get("cost", {})
    min_cost = optional_float(cost_limits.get("min"))
    if min_cost is not None:
        return min_cost

    info = market.get("info", {}) if isinstance(market, dict) else {}
    filters = info.get("filters", []) if isinstance(info, dict) else []
    for item in filters:
        if item.get("filterType") in {"MIN_NOTIONAL", "NOTIONAL"}:
            min_notional = optional_float(item.get("minNotional"))
            if min_notional is not None:
                return min_notional
    return None


def order_status(raw_order: dict) -> str:
    return str(raw_order.get("status", "")).lower()


def filled_amount(raw_order: dict) -> float:
    return safe_float(raw_order.get("filled"), 0.0)


def average_fill_price(raw_order: dict) -> float | None:
    average = optional_float(raw_order.get("average"))
    if average is not None:
        return average

    price = optional_float(raw_order.get("price"))
    cost = optional_float(raw_order.get("cost"))
    filled = filled_amount(raw_order)
    if cost is not None and filled > 0:
        return cost / filled
    return price


def build_client_order_id(prefix: str, symbol: str, side: OrderSide) -> str:
    clean_symbol = symbol.replace("/", "").replace("-", "").lower()
    suffix = uuid4().hex[:12]
    return f"{prefix}_{clean_symbol}_{side}_{suffix}"[:36]


def _coerce_request(request: OrderRequest | dict) -> OrderRequest:
    if isinstance(request, OrderRequest):
        return request

    return OrderRequest(
        symbol=str(request.get("symbol", "")),
        side=request.get("side", "buy"),
        order_type=request.get("order_type", request.get("type", "market")),
        amount=optional_float(request.get("amount")),
        quote_amount_usdt=optional_float(
            request.get("quote_amount_usdt", request.get("quote_usdt"))
        ),
        price=optional_float(request.get("price")),
        expected_price=optional_float(request.get("expected_price")),
        max_slippage_percent=optional_float(request.get("max_slippage_percent")),
        client_order_id=request.get("client_order_id"),
        idempotency_key=request.get("idempotency_key"),
        reduce_only=bool(request.get("reduce_only", False)),
        dry_run=bool(request.get("dry_run", False)),
        params=dict(request.get("params", {})),
    )


def _result_from_validation(
    request: OrderRequest,
    validation: OrderValidation,
    status: OrderStatus,
) -> OrderResult:
    return OrderResult(
        status=status,
        symbol=request.symbol,
        side=request.side,
        order_type=request.order_type,
        requested_amount=request.amount or 0.0,
        submitted_amount=0.0,
        filled_amount=0.0,
        remaining_amount=request.amount or 0.0,
        requested_price=request.price,
        average_fill_price=None,
        expected_price=request.expected_price,
        slippage_percent=None,
        order_id=None,
        client_order_id=request.client_order_id,
        idempotency_key=request.idempotency_key,
        attempts=0,
        latency_ms=0,
        raw_order=None,
        reasons=validation.reasons,
        warnings=validation.warnings,
        blockers=validation.blockers,
    )


def _blocked_result(
    request: OrderRequest,
    normalized: NormalizedOrder,
    blockers: list[str],
) -> OrderResult:
    return OrderResult(
        status="blocked",
        symbol=normalized.symbol,
        side=normalized.side,
        order_type=normalized.order_type,
        requested_amount=normalized.amount,
        submitted_amount=0.0,
        filled_amount=0.0,
        remaining_amount=normalized.amount,
        requested_price=normalized.price,
        average_fill_price=None,
        expected_price=normalized.expected_price,
        slippage_percent=None,
        order_id=None,
        client_order_id=normalized.client_order_id,
        idempotency_key=normalized.idempotency_key,
        attempts=0,
        latency_ms=0,
        raw_order=None,
        reasons=[],
        warnings=[],
        blockers=blockers,
    )


def _validated_result(
    request: OrderRequest,
    normalized: NormalizedOrder,
    reasons: list[str],
    warnings: list[str],
) -> OrderResult:
    return OrderResult(
        status="validated",
        symbol=normalized.symbol,
        side=normalized.side,
        order_type=normalized.order_type,
        requested_amount=normalized.amount,
        submitted_amount=0.0,
        filled_amount=0.0,
        remaining_amount=normalized.amount,
        requested_price=normalized.price,
        average_fill_price=None,
        expected_price=normalized.expected_price,
        slippage_percent=None,
        order_id=None,
        client_order_id=normalized.client_order_id,
        idempotency_key=normalized.idempotency_key,
        attempts=0,
        latency_ms=0,
        raw_order=None,
        reasons=_dedupe(reasons),
        warnings=_dedupe(warnings),
        blockers=[],
    )


def _clone_with_warning(result: OrderResult | None, warning: str) -> OrderResult:
    if result is None:
        return OrderResult(
            status="blocked",
            symbol="",
            side="buy",
            order_type="market",
            requested_amount=0.0,
            submitted_amount=0.0,
            filled_amount=0.0,
            remaining_amount=0.0,
            requested_price=None,
            average_fill_price=None,
            expected_price=None,
            slippage_percent=None,
            order_id=None,
            client_order_id=None,
            idempotency_key=None,
            attempts=0,
            latency_ms=0,
            raw_order=None,
            reasons=[],
            warnings=[warning],
            blockers=["Previous order result missing"],
        )

    payload = result.to_dict()
    payload.pop("success", None)
    payload["warnings"] = _dedupe(result.warnings + [warning])
    return OrderResult(**payload)


def safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _latency_ms(started: float) -> int:
    return max(0, int((monotonic() - started) * 1000))


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    deduped = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped
