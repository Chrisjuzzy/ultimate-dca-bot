from dataclasses import asdict, dataclass, field
from typing import Iterable

import pandas as pd

from analytics.performance import PerformanceReport, build_performance_report
from execution.exits import evaluate_exit
from paper.paper_exchange import PaperExchange, PaperExchangeConfig
from portfolio.positions import PositionManager
from risk.exposure import evaluate_exposure
from risk.position_sizing import WalletState, calculate_position_size
from risk.recovery import evaluate_recovery
from strategy.cooldown import CooldownContext, evaluate_cooldown
from strategy.indicators import add_indicators
from strategy.market_regime import detect_market_regime
from strategy.market_stress import calculate_market_stress
from strategy.scoring import score_opportunity
from strategy.signals import analyze_signals
from execution.entries import evaluate_entry


@dataclass(frozen=True)
class BacktestConfig:
    starting_equity_usdt: float = 1000.0
    warmup_candles: int = 220
    max_steps: int | None = None
    fee_rate: float = 0.001
    slippage_bps: float = 5.0
    min_history: int = 220
    allow_single_position_per_symbol: bool = True


@dataclass(frozen=True)
class BacktestEvent:
    index: int
    symbol: str
    timestamp: str
    event_type: str
    price: float
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class BacktestEquityPoint:
    index: int
    timestamp: str
    equity_usdt: float
    cash_usdt: float
    exposure_usdt: float
    drawdown_percent: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class BacktestResult:
    config: dict
    performance: PerformanceReport
    events: list[BacktestEvent]
    equity_path: list[BacktestEquityPoint]
    accepted_entries: int
    rejected_entries: int
    exit_events: int
    max_equity_drawdown_percent: float
    final_equity_usdt: float

    def to_dict(self) -> dict:
        return {
            "config": self.config,
            "performance": self.performance.to_dict(),
            "events": [event.to_dict() for event in self.events],
            "equity_path": [point.to_dict() for point in self.equity_path],
            "accepted_entries": self.accepted_entries,
            "rejected_entries": self.rejected_entries,
            "exit_events": self.exit_events,
            "max_equity_drawdown_percent": self.max_equity_drawdown_percent,
            "final_equity_usdt": self.final_equity_usdt,
        }


class Backtester:
    def __init__(self, config: BacktestConfig | None = None) -> None:
        self.config = config or BacktestConfig()

    def run(
        self,
        candles_by_symbol: dict[str, pd.DataFrame | Iterable],
    ) -> BacktestResult:
        exchange = PaperExchange(
            PaperExchangeConfig(
                initial_usdt=self.config.starting_equity_usdt,
                fee_rate=self.config.fee_rate,
                slippage_bps=self.config.slippage_bps,
            )
        )
        events: list[BacktestEvent] = []
        equity_path: list[BacktestEquityPoint] = []
        accepted = 0
        rejected = 0
        exits = 0
        cash_usdt = self.config.starting_equity_usdt
        peak_equity_usdt = self.config.starting_equity_usdt
        normalized = {
            symbol: add_indicators(normalize_candles(candles))
            for symbol, candles in candles_by_symbol.items()
        }
        max_length = min(len(df) for df in normalized.values()) if normalized else 0
        end = max_length
        if self.config.max_steps is not None:
            end = min(end, self.config.warmup_candles + self.config.max_steps)

        open_positions: dict[str, dict] = {}
        closed_positions: dict[str, dict] = {}
        recent_loss_count = 0

        for index in range(self.config.warmup_candles, end):
            histories = {
                symbol: df.iloc[: index + 1]
                for symbol, df in normalized.items()
            }
            signals_by_symbol = {
                symbol: analyze_signals(history)
                for symbol, history in histories.items()
            }
            regimes_by_symbol = {
                symbol: detect_market_regime(history)
                for symbol, history in histories.items()
            }
            global_stress = calculate_market_stress(signals_by_symbol, regimes_by_symbol)

            for symbol, df in normalized.items():
                row = df.iloc[index]
                price = float(row["close"])
                timestamp = str(row.get("timestamp", index))
                exchange.set_price(symbol, price)

                signals = signals_by_symbol[symbol]
                regime = regimes_by_symbol[symbol]
                recovery = evaluate_recovery(
                    {
                        "current_equity_usdt": cash_usdt + mark_to_market(open_positions),
                        "peak_equity_usdt": peak_equity_usdt,
                        "recent_loss_count": recent_loss_count,
                    },
                    regime=regime,
                )
                score = score_opportunity(signals)
                cooldown = evaluate_cooldown(
                    score,
                    regime,
                    context=CooldownContext(
                        recent_loss_count=recent_loss_count,
                        defensive_mode=recovery.mode in {"defensive", "survival"},
                    ),
                )

                if symbol in open_positions:
                    position = open_positions[symbol]
                    position["current_price"] = price
                    exit_decision = evaluate_exit(
                        {
                            "position": position,
                            "signals": signals.to_dict(),
                            "regime": regime.to_dict(),
                        }
                    )
                    if exit_decision.action == "update_trailing_stop":
                        position["trailing_stop_price"] = exit_decision.updated_trailing_stop_price
                    elif exit_decision.should_exit:
                        exits += 1
                        realized = (
                            price - position["entry_price"]
                        ) * position["remaining_quantity"]
                        fee = price * position["remaining_quantity"] * self.config.fee_rate
                        realized -= fee
                        cash_usdt += price * position["remaining_quantity"] - fee
                        closed = {
                            **position,
                            "status": "closed",
                            "current_price": price,
                            "remaining_quantity": 0.0,
                            "realized_pnl_usdt": realized,
                            "total_fees_usdt": position.get("total_fees_usdt", 0.0) + fee,
                            "closed_at": timestamp,
                            "exit_reason": exit_decision.action,
                        }
                        closed_positions[f"{symbol}#{index}"] = closed
                        open_positions.pop(symbol, None)
                        recent_loss_count = recent_loss_count + 1 if realized < 0 else 0
                        events.append(
                            BacktestEvent(
                                index=index,
                                symbol=symbol,
                                timestamp=timestamp,
                                event_type="exit",
                                price=price,
                                details=exit_decision.to_dict(),
                            )
                        )
                    continue

                wallet = WalletState(
                    total_usdt=cash_usdt + mark_to_market(open_positions),
                    free_usdt=cash_usdt,
                    current_exposure_usdt=sum(
                        item["current_price"] * item["remaining_quantity"]
                        for item in open_positions.values()
                    ),
                )
                sizing = calculate_position_size(
                    wallet=wallet,
                    score=score,
                    regime=regime,
                )
                exposure = evaluate_exposure(
                    symbol=symbol,
                    proposed_size=sizing,
                    context={
                        "wallet_total_usdt": wallet.total_usdt,
                        "open_positions": open_positions,
                    },
                )
                entry = evaluate_entry(
                    {
                        "symbol": symbol,
                        "signals": signals.to_dict(),
                        "score": score.to_dict(),
                        "regime": regime.to_dict(),
                        "cooldown": cooldown.to_dict(),
                        "position_size": sizing.to_dict(),
                        "exposure": exposure.to_dict(),
                        "recovery": recovery.to_dict(),
                        "market_stress_score": global_stress.score,
                    }
                )

                if not entry.can_enter:
                    rejected += 1
                    events.append(
                        BacktestEvent(
                            index=index,
                            symbol=symbol,
                            timestamp=timestamp,
                            event_type="entry_rejected",
                            price=price,
                            details={"blockers": entry.blockers, "score": entry.score},
                        )
                    )
                    continue

                accepted += 1
                notional = entry.approved_size
                fee = notional * self.config.fee_rate
                if notional + fee > cash_usdt:
                    rejected += 1
                    events.append(
                        BacktestEvent(
                            index=index,
                            symbol=symbol,
                            timestamp=timestamp,
                            event_type="entry_rejected",
                            price=price,
                            details={"blockers": ["Insufficient paper cash after fees"]},
                        )
                    )
                    continue
                cash_usdt -= notional + fee
                quantity = notional / price if price > 0 else 0.0
                open_positions[symbol] = {
                    "symbol": symbol,
                    "status": "open",
                    "entry_price": price,
                    "current_price": price,
                    "exposure_usdt": notional,
                    "quantity": quantity,
                    "remaining_quantity": quantity,
                    "opened_at": timestamp,
                    "entry_type": entry.entry_type,
                    "entry_grade": entry.entry_grade,
                    "entry_score": entry.score,
                    "regime_at_entry": regime.regime,
                    "highest_price": price,
                    "total_fees_usdt": fee,
                    "events": [],
                }
                events.append(
                    BacktestEvent(
                        index=index,
                        symbol=symbol,
                        timestamp=timestamp,
                        event_type="entry",
                        price=price,
                        details=entry.to_dict(),
                    )
                )

            equity = cash_usdt + mark_to_market(open_positions)
            peak_equity_usdt = max(peak_equity_usdt, equity)
            drawdown = (
                0.0
                if peak_equity_usdt <= 0
                else max(0.0, (peak_equity_usdt - equity) / peak_equity_usdt * 100)
            )
            equity_path.append(
                BacktestEquityPoint(
                    index=index,
                    timestamp=str(index),
                    equity_usdt=round(equity, 8),
                    cash_usdt=round(cash_usdt, 8),
                    exposure_usdt=round(mark_to_market(open_positions), 8),
                    drawdown_percent=round(drawdown, 4),
                )
            )

        all_positions = {**closed_positions, **open_positions}
        performance = build_performance_report(
            {"positions": all_positions, "updated_at": None},
            starting_equity_usdt=self.config.starting_equity_usdt,
        )
        max_equity_drawdown = max(
            [point.drawdown_percent for point in equity_path],
            default=0.0,
        )
        final_equity = equity_path[-1].equity_usdt if equity_path else self.config.starting_equity_usdt
        return BacktestResult(
            config=asdict(self.config),
            performance=performance,
            events=events,
            equity_path=equity_path,
            accepted_entries=accepted,
            rejected_entries=rejected,
            exit_events=exits,
            max_equity_drawdown_percent=round(max_equity_drawdown, 4),
            final_equity_usdt=round(final_equity, 8),
        )


def normalize_candles(candles: pd.DataFrame | Iterable) -> pd.DataFrame:
    if isinstance(candles, pd.DataFrame):
        df = candles.copy()
    else:
        df = pd.DataFrame(
            candles,
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
    for column in ["open", "high", "low", "close", "volume"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    return df.dropna(subset=["open", "high", "low", "close", "volume"]).reset_index(drop=True)


def mark_to_market(open_positions: dict[str, dict]) -> float:
    return sum(
        max(0.0, float(position.get("current_price", 0.0)))
        * max(0.0, float(position.get("remaining_quantity", 0.0)))
        for position in open_positions.values()
    )
