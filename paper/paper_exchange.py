from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from itertools import count
from typing import Literal


PaperSide = Literal["buy", "sell"]
PaperOrderType = Literal["market", "limit"]


@dataclass(frozen=True)
class PaperExchangeConfig:
    initial_usdt: float = 10000.0
    fee_rate: float = 0.001
    slippage_bps: float = 5.0
    fill_ratio: float = 1.0
    min_notional_usdt: float = 5.0
    amount_precision: int = 6
    price_precision: int = 2


@dataclass(frozen=True)
class PaperFill:
    order_id: str
    symbol: str
    side: PaperSide
    amount: float
    price: float
    cost: float
    fee_usdt: float
    timestamp: str

    def to_dict(self) -> dict:
        return asdict(self)


class PaperExchange:
    def __init__(self, config: PaperExchangeConfig | None = None) -> None:
        self.config = config or PaperExchangeConfig()
        self.prices: dict[str, float] = {
            "BTC/USDT": 65000.0,
            "ETH/USDT": 3000.0,
        }
        self.balances: dict[str, dict[str, float]] = {
            "USDT": {
                "free": self.config.initial_usdt,
                "used": 0.0,
                "total": self.config.initial_usdt,
            }
        }
        self.orders: dict[str, dict] = {}
        self.fills: list[PaperFill] = []
        self._counter = count(1)
        self.markets = self._build_markets()

    def load_markets(self) -> dict:
        return self.markets

    def set_price(self, symbol: str, price: float) -> None:
        self.prices[symbol] = max(0.0, float(price))

    def fetch_ticker(self, symbol: str) -> dict:
        price = self.prices.get(symbol)
        if price is None:
            raise ValueError(f"No paper price available for {symbol}")
        return {
            "symbol": symbol,
            "last": price,
            "bid": price * 0.9995,
            "ask": price * 1.0005,
            "timestamp": int(datetime.now(UTC).timestamp() * 1000),
        }

    def fetch_balance(self) -> dict:
        free = {asset: values["free"] for asset, values in self.balances.items()}
        used = {asset: values["used"] for asset, values in self.balances.items()}
        total = {asset: values["total"] for asset, values in self.balances.items()}
        return {
            "free": free,
            "used": used,
            "total": total,
        }

    def amount_to_precision(self, symbol: str, amount: float) -> str:
        precision = self.markets.get(symbol, {}).get("precision", {}).get(
            "amount",
            self.config.amount_precision,
        )
        return f"{max(0.0, amount):.{precision}f}"

    def price_to_precision(self, symbol: str, price: float) -> str:
        precision = self.markets.get(symbol, {}).get("precision", {}).get(
            "price",
            self.config.price_precision,
        )
        return f"{max(0.0, price):.{precision}f}"

    def create_order(
        self,
        symbol: str,
        order_type: PaperOrderType,
        side: PaperSide,
        amount: float,
        price: float | None = None,
        params: dict | None = None,
    ) -> dict:
        params = params or {}
        amount = float(self.amount_to_precision(symbol, amount))
        fill_amount = round(amount * clamp_float(self.config.fill_ratio, 0.0, 1.0), 8)
        execution_price = self._execution_price(symbol, side, order_type, price)
        cost = fill_amount * execution_price
        fee = cost * self.config.fee_rate

        if cost < self.config.min_notional_usdt:
            raise ValueError(
                f"Paper order notional {cost:.4f} below minimum {self.config.min_notional_usdt:.4f}"
            )

        base, quote = split_symbol(symbol)
        order_id = str(next(self._counter))
        status = "closed" if fill_amount >= amount else "open"
        remaining = max(0.0, amount - fill_amount)

        if side == "buy":
            self._require_balance(quote, cost + fee)
            self._add_balance(quote, -(cost + fee))
            self._add_balance(base, fill_amount)
        else:
            self._require_balance(base, fill_amount)
            self._add_balance(base, -fill_amount)
            self._add_balance(quote, cost - fee)

        fill = PaperFill(
            order_id=order_id,
            symbol=symbol,
            side=side,
            amount=fill_amount,
            price=execution_price,
            cost=cost,
            fee_usdt=fee,
            timestamp=datetime.now(UTC).isoformat(),
        )
        self.fills.append(fill)

        order = {
            "id": order_id,
            "clientOrderId": params.get("newClientOrderId"),
            "symbol": symbol,
            "type": order_type,
            "side": side,
            "status": status,
            "amount": amount,
            "filled": fill_amount,
            "remaining": remaining,
            "price": price,
            "average": execution_price,
            "cost": cost,
            "fee": {
                "cost": fee,
                "currency": quote,
            },
            "fees": [
                {
                    "cost": fee,
                    "currency": quote,
                }
            ],
            "timestamp": int(datetime.now(UTC).timestamp() * 1000),
            "datetime": datetime.now(UTC).isoformat(),
            "info": {
                "paper": True,
                "params": params,
            },
        }
        self.orders[order_id] = order
        return order

    def fetch_order(self, order_id: str, symbol: str | None = None) -> dict:
        order = self.orders.get(str(order_id))
        if order is None:
            raise KeyError(f"Paper order {order_id} not found")
        if symbol is not None and order.get("symbol") != symbol:
            raise KeyError(f"Paper order {order_id} does not match {symbol}")
        return order

    def fetch_open_orders(self, symbol: str | None = None) -> list[dict]:
        orders = [order for order in self.orders.values() if order["status"] == "open"]
        if symbol is not None:
            orders = [order for order in orders if order["symbol"] == symbol]
        return orders

    def _execution_price(
        self,
        symbol: str,
        side: PaperSide,
        order_type: PaperOrderType,
        price: float | None,
    ) -> float:
        reference = price if order_type == "limit" and price else self.prices.get(symbol)
        if reference is None or reference <= 0:
            raise ValueError(f"No valid paper price available for {symbol}")

        slippage = self.config.slippage_bps / 10000
        if side == "buy":
            return round(reference * (1 + slippage), self.config.price_precision)
        return round(reference * (1 - slippage), self.config.price_precision)

    def _require_balance(self, asset: str, amount: float) -> None:
        available = self.balances.get(asset, {"free": 0.0})["free"]
        if available + 1e-12 < amount:
            raise ValueError(
                f"Insufficient paper balance for {asset}: need {amount:.8f}, have {available:.8f}"
            )

    def _add_balance(self, asset: str, delta: float) -> None:
        self.balances.setdefault(asset, {"free": 0.0, "used": 0.0, "total": 0.0})
        self.balances[asset]["free"] += delta
        self.balances[asset]["total"] = (
            self.balances[asset]["free"] + self.balances[asset]["used"]
        )

    def _build_markets(self) -> dict:
        return {
            "BTC/USDT": self._market("BTC/USDT"),
            "ETH/USDT": self._market("ETH/USDT"),
        }

    def _market(self, symbol: str) -> dict:
        return {
            "symbol": symbol,
            "base": split_symbol(symbol)[0],
            "quote": split_symbol(symbol)[1],
            "precision": {
                "amount": self.config.amount_precision,
                "price": self.config.price_precision,
            },
            "limits": {
                "amount": {
                    "min": 10 ** -self.config.amount_precision,
                },
                "cost": {
                    "min": self.config.min_notional_usdt,
                },
            },
            "info": {
                "paper": True,
            },
        }


def split_symbol(symbol: str) -> tuple[str, str]:
    if "/" not in symbol:
        raise ValueError(f"Invalid symbol {symbol}")
    base, quote = symbol.split("/", 1)
    return base, quote


def clamp_float(value: object, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = minimum
    return max(minimum, min(maximum, number))
