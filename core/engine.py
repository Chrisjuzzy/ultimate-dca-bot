from dataclasses import asdict, dataclass, field
from typing import Iterable

from analytics.trade_history import TradeHistoryStore
from analytics.trade_journal import (
    TradeJournal,
    journal_entry_from_entry_decision,
    journal_entry_from_exit_decision,
)
from execution.entries import EntryDecision, evaluate_entry
from execution.exits import ExitDecision, evaluate_exit
from execution.order_manager import OrderRequest, SafeOrderManager
from paper.paper_exchange import PaperExchange
from portfolio.positions import PositionManager
from risk.exposure import ExposureContext, evaluate_exposure
from risk.position_sizing import SizingContext, WalletState, calculate_position_size
from risk.recovery import RecoveryContext, evaluate_recovery
from strategy.cooldown import CooldownContext, evaluate_cooldown
from strategy.market_regime import detect_market_regime
from strategy.market_stress import MarketStressState, calculate_market_stress
from strategy.scoring import score_opportunity
from strategy.signals import analyze_signals


@dataclass(frozen=True)
class EngineConfig:
    paper_mode: bool = True
    quote_asset: str = "USDT"
    default_expected_price_key: str = "close"
    allow_live_orders: bool = False


@dataclass(frozen=True)
class EngineCycleResult:
    symbol: str
    entry_decision: dict | None = None
    exit_decision: dict | None = None
    order_result: dict | None = None
    position_result: dict | None = None
    journal_events: int = 0
    warnings: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class TradingEngine:
    def __init__(
        self,
        exchange=None,
        config: EngineConfig | None = None,
        journal: TradeJournal | None = None,
        trade_history: TradeHistoryStore | None = None,
        position_manager: PositionManager | None = None,
    ) -> None:
        self.config = config or EngineConfig()
        self.exchange = exchange or PaperExchange()
        self.journal = journal or TradeJournal()
        self.trade_history = trade_history or TradeHistoryStore()
        self.position_manager = position_manager or PositionManager()
        self.order_manager = SafeOrderManager(self.exchange)

    def evaluate_symbol(
        self,
        symbol: str,
        candles,
        wallet: WalletState | dict,
        recovery_context: RecoveryContext | dict | None = None,
        cooldown_context: CooldownContext | None = None,
        sizing_context: SizingContext | None = None,
        exposure_context: ExposureContext | dict | None = None,
        global_stress: MarketStressState | dict | None = None,
    ) -> EngineCycleResult:
        signals = analyze_signals(candles)
        score = score_opportunity(signals)
        regime = detect_market_regime(candles)
        recovery = evaluate_recovery(recovery_context or {}, regime=regime)
        cooldown = evaluate_cooldown(
            score,
            regime,
            context=cooldown_context or CooldownContext(
                recent_loss_count=recovery.recent_loss_count,
                defensive_mode=recovery.mode in {"defensive", "survival"},
            ),
        )
        sizing = calculate_position_size(
            wallet=wallet,
            score=score,
            regime=regime,
            context=sizing_context or SizingContext(
                defensive_mode=recovery.mode in {"defensive", "survival"},
                recent_loss_count=recovery.recent_loss_count,
            ),
        )
        exposure = evaluate_exposure(
            symbol=symbol,
            proposed_size=sizing,
            context=exposure_context or {
                "wallet_total_usdt": _wallet_total(wallet),
                "open_positions": {},
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
                "market_stress_score": _stress_score(global_stress),
            }
        )
        self.journal.append(journal_entry_from_entry_decision(entry.to_dict()))
        return EngineCycleResult(
            symbol=symbol,
            entry_decision=entry.to_dict(),
            journal_events=1,
            warnings=entry.warnings,
            blockers=entry.blockers,
        )

    def paper_enter(
        self,
        entry_decision: EntryDecision | dict,
        expected_price: float,
    ) -> EngineCycleResult:
        decision = entry_decision.to_dict() if isinstance(entry_decision, EntryDecision) else dict(entry_decision)
        symbol = decision.get("symbol", "")
        if not decision.get("can_enter"):
            return EngineCycleResult(
                symbol=symbol,
                entry_decision=decision,
                blockers=["Entry decision does not allow entry"],
            )

        order = self.order_manager.place_order(
            OrderRequest(
                symbol=symbol,
                side="buy",
                quote_amount_usdt=float(decision.get("approved_size", 0.0)),
                expected_price=expected_price,
                dry_run=False,
            )
        )
        if not order.success or order.filled_amount <= 0:
            return EngineCycleResult(
                symbol=symbol,
                entry_decision=decision,
                order_result=order.to_dict(),
                blockers=order.blockers,
                warnings=order.warnings,
            )

        position = self.position_manager.open_position(
            symbol=symbol,
            entry_price=order.average_fill_price or expected_price,
            quantity=order.filled_amount,
            entry_decision=decision,
            fee_usdt=_order_fee(order.raw_order),
        )
        self.journal.record(
            "position_opened",
            symbol=symbol,
            status="open",
            side="buy",
            quantity=order.filled_amount,
            price=order.average_fill_price or expected_price,
            entry_type=decision.get("entry_type", ""),
            score=int(decision.get("score", 0) or 0),
            market_stress_score=int(decision.get("market_stress_score", 0) or 0),
            reasons=list(decision.get("reasons", [])),
            warnings=list(decision.get("warnings", [])),
            payload=position.to_dict(),
        )
        self.journal.record(
            "paper_fill",
            symbol=symbol,
            status=order.status,
            side="buy",
            quantity=order.filled_amount,
            price=order.average_fill_price or expected_price,
            payload=order.to_dict(),
        )
        return EngineCycleResult(
            symbol=symbol,
            entry_decision=decision,
            order_result=order.to_dict(),
            position_result=position.to_dict(),
            journal_events=1,
            warnings=order.warnings,
            blockers=order.blockers,
        )

    def paper_exit(
        self,
        symbol: str,
        exit_decision: ExitDecision | dict,
        fill_price: float | None = None,
        fee_usdt: float = 0.0,
    ) -> EngineCycleResult:
        decision = exit_decision.to_dict() if isinstance(exit_decision, ExitDecision) else dict(exit_decision)
        if not decision.get("should_exit", False):
            return EngineCycleResult(
                symbol=symbol,
                exit_decision=decision,
                blockers=["Exit decision does not require an exit"],
            )

        position = self.position_manager.apply_exit(
            symbol=symbol,
            exit_decision=decision,
            fill_price=fill_price,
            fee_usdt=fee_usdt,
            trade_history_manager=self.trade_history,
        )
        self.journal.record(
            "position_closed" if position.status == "closed" else "exit_decision",
            symbol=symbol,
            status=position.status,
            side="sell",
            quantity=decision.get("quantity_to_exit", 0.0),
            price=fill_price or position.current_price,
            pnl_usdt=position.realized_pnl_usdt,
            exit_reason=position.exit_reason or decision.get("exit_reason", ""),
            payload=position.to_dict(),
        )
        return EngineCycleResult(
            symbol=symbol,
            exit_decision=decision,
            position_result=position.to_dict(),
            journal_events=1,
            warnings=decision.get("warnings", []),
            blockers=decision.get("blockers", []),
        )

    def evaluate_position_exit(
        self,
        symbol: str,
        signals: dict,
        regime: dict,
        market_stress_score: int | None = None,
    ) -> EngineCycleResult:
        snapshot = self.position_manager.load()
        position = snapshot.positions.get(symbol)
        if position is None:
            return EngineCycleResult(symbol=symbol, blockers=["No position found"])

        exit_decision = evaluate_exit(
            {
                "position": position.to_dict(),
                "signals": signals,
                "regime": regime,
                "market_stress_score": market_stress_score,
            }
        )
        self.journal.append(journal_entry_from_exit_decision(exit_decision.to_dict()))
        return EngineCycleResult(
            symbol=symbol,
            exit_decision=exit_decision.to_dict(),
            journal_events=1,
            warnings=exit_decision.warnings,
            blockers=exit_decision.blockers,
        )


def run_watchlist_evaluation(
    engine: TradingEngine,
    candles_by_symbol: dict[str, Iterable],
    wallet: WalletState | dict,
) -> list[EngineCycleResult]:
    signals_by_symbol = {
        symbol: analyze_signals(candles)
        for symbol, candles in candles_by_symbol.items()
    }
    regimes_by_symbol = {
        symbol: detect_market_regime(candles)
        for symbol, candles in candles_by_symbol.items()
    }
    global_stress = calculate_market_stress(signals_by_symbol, regimes_by_symbol)
    results = []
    for symbol, candles in candles_by_symbol.items():
        results.append(
            engine.evaluate_symbol(
                symbol=symbol,
                candles=candles,
                wallet=wallet,
                global_stress=global_stress,
            )
        )
    return results


def _stress_score(global_stress: MarketStressState | dict | None) -> int | None:
    if global_stress is None:
        return None
    if isinstance(global_stress, MarketStressState):
        return global_stress.score
    return int(global_stress.get("score", 0))


def _wallet_total(wallet: WalletState | dict) -> float:
    if isinstance(wallet, WalletState):
        return wallet.total_usdt
    return float(wallet.get("total_usdt", wallet.get("total", 0.0)))


def _order_fee(raw_order: dict | None) -> float:
    if not raw_order:
        return 0.0
    fee = raw_order.get("fee", {})
    if isinstance(fee, dict):
        return float(fee.get("cost", 0.0) or 0.0)
    return 0.0
