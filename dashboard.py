import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from analytics.performance import build_performance_report
from analytics.trade_history import TradeHistoryManager, TimeFilter
from analytics.trade_journal import TradeJournal
from alerts.telegram_alerts import send_telegram, telegram_is_configured
from config import REFRESH_SECONDS
from core.runtime_control import disable_bot, enable_bot, load_control, request_restart, set_mode
from portfolio.positions import PositionManager, PositionSnapshot
from market_data import market_data
from utils.helpers import tail_lines
from utils.state import POSITIONS_FILE


LOG_FILE = Path("logs") / "bot.log"
DB_FILE = Path("database") / "trading.db"
JOURNAL_FILE = Path("data") / "trade_journal.jsonl"
PAPER_REPORT_FILE = Path("data") / "paper_report.json"
PAPER_METRICS_FILE = Path("data") / "paper_metrics.jsonl"
TRADE_HISTORY_FILE = Path("data") / "trade_history.jsonl"
STARTING_EQUITY_USDT = 1000.0


st.set_page_config(
    page_title="Ultimate DCA Bot Command Center",
    layout="wide",
)
st.html(f'<meta http-equiv="refresh" content="{REFRESH_SECONDS}">')


def inject_styles() -> None:
    st.markdown(
        """
        <style>
        :root {
            --bg: #0e1117;
            --panel: #161b22;
            --panel-soft: #1d2430;
            --border: rgba(148, 163, 184, 0.18);
            --text: #f8fafc;
            --muted: #94a3b8;
            --green: #00ff99;
            --green-soft: rgba(0, 255, 153, 0.12);
            --red: #ff5c7a;
            --red-soft: rgba(255, 92, 122, 0.12);
            --blue: #56b6ff;
            --blue-soft: rgba(86, 182, 255, 0.12);
            --gold: #f5c542;
            --gold-soft: rgba(245, 197, 66, 0.12);
        }

        .block-container {
            padding-top: 1.2rem;
            padding-bottom: 2.4rem;
            max-width: 1500px;
        }

        .hero {
            border: 1px solid var(--border);
            border-radius: 24px;
            padding: 28px 30px;
            margin-bottom: 12px;
            background:
                radial-gradient(circle at top right, rgba(0, 255, 153, 0.16), transparent 32%),
                linear-gradient(135deg, rgba(22, 27, 34, 0.98), rgba(14, 17, 23, 0.98));
            box-shadow: 0 22px 70px rgba(0, 0, 0, 0.35);
        }

        .hero h1 {
            margin: 4px 0 6px 0;
            font-size: clamp(2rem, 5vw, 4.2rem);
            letter-spacing: -0.06em;
            color: var(--text);
        }

        .hero p {
            color: var(--muted);
            max-width: 760px;
            font-size: 1.02rem;
        }

        .eyebrow {
            color: var(--green);
            font-size: 0.78rem;
            letter-spacing: 0.18em;
            font-weight: 800;
        }

        .trade-win {
            color: var(--green);
            font-weight: bold;
        }

        .trade-loss {
            color: var(--red);
            font-weight: bold;
        }

        .mode-banner {
            display: flex;
            gap: 14px;
            align-items: center;
            justify-content: space-between;
            padding: 12px 16px;
            border-radius: 16px;
            border: 1px solid var(--border);
            margin: 12px 0 18px 0;
            box-shadow: inset 0 0 28px rgba(255, 255, 255, 0.015);
        }

        .mode-banner span,
        .mode-banner strong {
            font-weight: 800;
        }

        .mode-banner em {
            color: var(--muted);
            font-style: normal;
            font-size: 0.9rem;
        }

        .mode-paper {
            background: linear-gradient(90deg, var(--blue-soft), rgba(22, 27, 34, 0.88));
            border-color: rgba(86, 182, 255, 0.32);
        }

        .mode-live {
            background: linear-gradient(90deg, var(--red-soft), rgba(22, 27, 34, 0.88));
            border-color: rgba(255, 92, 122, 0.38);
        }

        .status-grid {
            display: grid;
            grid-template-columns: repeat(5, minmax(120px, 1fr));
            gap: 12px;
            margin: 12px 0 22px 0;
        }

        .status-card {
            background: linear-gradient(180deg, rgba(255, 255, 255, 0.035), rgba(255, 255, 255, 0.012));
            border: 1px solid var(--border);
            border-radius: 18px;
            padding: 14px 15px;
            min-height: 94px;
            box-shadow: 0 16px 38px rgba(0, 0, 0, 0.22);
        }

        .status-card span {
            display: block;
            color: var(--muted);
            font-size: 0.75rem;
            letter-spacing: 0.05em;
            text-transform: uppercase;
        }

        .status-card strong {
            display: block;
            margin-top: 12px;
            color: var(--text);
            font-size: 1.22rem;
            line-height: 1.08;
        }

        .status-card.good {
            border-color: rgba(0, 255, 153, 0.32);
            box-shadow: 0 0 26px rgba(0, 255, 153, 0.06);
        }

        .status-card.good strong {
            color: var(--green);
        }

        .status-card.warn {
            border-color: rgba(245, 197, 66, 0.35);
        }

        .status-card.warn strong {
            color: var(--gold);
        }

        .status-card.danger {
            border-color: rgba(255, 92, 122, 0.38);
            box-shadow: 0 0 26px rgba(255, 92, 122, 0.06);
        }

        .status-card.danger strong {
            color: var(--red);
        }

        .status-card.info strong {
            color: var(--blue);
        }

        div[data-testid="stMetric"] {
            background: rgba(22, 27, 34, 0.72);
            border: 1px solid var(--border);
            border-radius: 18px;
            padding: 14px 16px;
            box-shadow: 0 14px 36px rgba(0, 0, 0, 0.20);
        }

        div[data-testid="stButton"] > button {
            border-radius: 14px;
            border: 1px solid rgba(0, 255, 153, 0.28);
            background: rgba(0, 255, 153, 0.08);
            color: #f8fafc;
            font-weight: 800;
            min-height: 46px;
        }

        div[data-testid="stButton"] > button:hover {
            border-color: rgba(0, 255, 153, 0.65);
            color: var(--green);
        }

        .telegram-card {
            background: linear-gradient(180deg, rgba(86, 182, 255, 0.08), rgba(22, 27, 34, 0.96));
            border: 1px solid rgba(86, 182, 255, 0.22);
            border-radius: 20px;
            padding: 18px;
            box-shadow: 0 16px 50px rgba(0, 0, 0, 0.15);
            min-height: 320px;
        }

        .telegram-card h3 {
            margin-bottom: 12px;
            color: var(--text);
        }

        .telegram-card ul {
            padding-left: 18px;
            color: var(--muted);
        }

        .telegram-card li {
            margin-bottom: 8px;
        }

        div[data-testid="stExpander"] {
            border: 1px solid var(--border);
            border-radius: 18px;
            overflow: hidden;
            background: rgba(22, 27, 34, 0.42);
        }

        textarea,
        .stDataFrame {
            border-radius: 16px;
        }

        @media (max-width: 900px) {
            .status-grid {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }
            .hero {
                padding: 22px 18px;
            }
            .mode-banner {
                display: block;
            }
            .mode-banner strong,
            .mode-banner em {
                display: block;
                margin-top: 8px;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def main() -> None:
    try:
        inject_styles()
        snapshot = safe_load_positions()
        journal = TradeJournal()
        journal_entries = journal.load(limit=200)
        performance = build_performance_report(snapshot, starting_equity_usdt=0)
        health = build_health_state(journal_entries)
        control = load_control()
        paper_report = load_paper_report()
        trade_history_mgr = TradeHistoryManager(TRADE_HISTORY_FILE)

        render_hero_section(control)
        render_status_bar(control, health, paper_report, journal_entries, snapshot, trade_history_mgr)
        render_center_sections(snapshot, performance, paper_report, trade_history_mgr, control)
        render_bottom_sections(trade_history_mgr, health, control, journal_entries)
    except Exception as e:
        st.error(f"Dashboard error: {str(e)}")
        st.write("Attempting to recover... Please refresh the page.")


def render_header() -> None:
    st.markdown(
        """
        <div class="hero">
            <div>
                <div class="eyebrow">PROFESSIONAL TRADING OPERATOR</div>
                <h1>Ultimate DCA Bot</h1>
                <p>Operator-grade trading system. Risk-first architecture. Paper validation. Professional trade history.</p>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.caption(
        f"Auto-refresh every {REFRESH_SECONDS}s | Last check: "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )


def render_hero_section(control: dict) -> None:
    render_header()
    render_mode_banner(control)


def render_mode_banner(control: dict) -> None:
    mode = str(control.get("mode", "paper")).upper()
    enabled = bool(control.get("enabled", False))
    mode_class = "mode-live" if mode == "LIVE" else "mode-paper"
    state = "ENABLED" if enabled else "PAUSED"
    st.markdown(
        f"""
        <div class="mode-banner {mode_class}">
            <span>{mode} MODE</span>
            <strong>{state}</strong>
            <em>Do not expose this dashboard publicly without authentication and HTTPS.</em>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_status_bar(
    control: dict,
    health: dict,
    paper_report: dict,
    journal_entries,
    snapshot,
    trade_history_mgr,
) -> None:
    enabled = bool(control.get("enabled", False))
    latest_entry = latest_payload(journal_entries, {"entry_decision", "entry_rejected"})
    mode = str(control.get("mode", "paper")).upper()
    state_mode = "PAUSED" if not enabled else mode
    regime = str(latest_entry.get("regime") or infer_btc_regime() or "unknown").upper()
    score = int(latest_entry.get("score", 0) or 0)
    risk_mode = str(latest_entry.get("recovery_mode") or "normal").upper()
    stress_score = int(latest_entry.get("market_stress_score", estimate_market_stress_score()) or 0)
    stress_label = "HIGH" if stress_score >= 60 else "LOW"

    cards = [
        ("Mode", state_mode, "warn" if state_mode == "PAUSED" else "good"),
        ("Regime", regime, regime_class(regime)),
        ("Score", str(score), "good" if score >= 82 else "warn"),
        ("Risk Mode", risk_mode, "danger" if risk_mode in {"SURVIVAL", "PAUSED"} else ("warn" if risk_mode in {"DEFENSIVE", "REDUCED"} else "good")),
        ("Market Stress", f"{stress_label} ({stress_score})", "danger" if stress_label == "HIGH" else "info"),
    ]
    cols = st.columns(len(cards))
    for col, (label, value, klass) in zip(cols, cards):
        delta = None
        if label == "Score":
            delta = ">=82 target" if int(value) >= 82 else "<82 wait"
        with col:
            col.metric(label, value, delta=delta)


def render_bot_control_panel(control: dict) -> None:
    st.subheader("Bot Control")
    enabled = bool(control.get("enabled", False))
    mode = str(control.get("mode", "paper"))

    status_col, mode_col, updated_col, restart_col = st.columns(4)
    status_col.metric("Trading Control", "Enabled" if enabled else "Paused")
    mode_col.metric("Mode", mode.upper())
    updated_col.metric("Updated", short_timestamp(control.get("updated_at")))
    restart_col.metric("Restart Nonce", control.get("restart_nonce", 0))

    if enabled:
        st.success("Bot trading loop is enabled from dashboard control.")
    else:
        st.warning("Bot trading loop is paused. The process can stay alive for monitoring.")

    start_col, stop_col, restart_button_col, mode_button_col = st.columns(4)
    with start_col:
        if st.button("START BOT", use_container_width=True):
            enable_bot()
            st.rerun()
    with stop_col:
        if st.button("STOP BOT", use_container_width=True):
            disable_bot()
            st.rerun()
    with restart_button_col:
        if st.button("SOFT RESTART", use_container_width=True):
            request_restart()
            st.rerun()
    with mode_button_col:
        next_mode = "live" if mode == "paper" else "paper"
        if st.button(f"MODE: {next_mode.upper()}", use_container_width=True):
            set_mode(next_mode)
            st.rerun()

    st.caption("Security: use on trusted local network or VPS with authentication only.")


def render_center_sections(snapshot, performance, paper_report, trade_history_mgr, control) -> None:
    st.subheader("Live Market + Portfolio")
    left_col, right_col = st.columns([1.6, 1], gap="large")
    with left_col:
        render_live_market_trade_card(snapshot)
    with right_col:
        render_portfolio_panel(snapshot, performance, paper_report, trade_history_mgr, control)


def render_telegram_command_center() -> None:
    st.markdown(
        """
        <div class="telegram-card">
            <h3>Telegram Command Center</h3>
            <p>Use these bot commands for remote control and status checks.</p>
            <ul>
                <li><strong>/startbot</strong> â€“ start the strategy loop</li>
                <li><strong>/stopbot</strong> â€“ stop trading and pause the loop</li>
                <li><strong>/restartbot</strong> â€“ request a soft restart</li>
                <li><strong>/status</strong> â€“ current bot + health summary</li>
                <li><strong>/positions</strong> â€“ open position snapshot</li>
                <li><strong>/pnl</strong> â€“ todayâ€™s profit and loss</li>
                <li><strong>/risk</strong> â€“ current risk posture</li>
                <li><strong>/brain</strong> â€“ analysis and regime status</li>
                <li><strong>/health</strong> â€“ system health and errors</li>
                <li><strong>/lasttrades</strong> â€“ recent trade history</li>
            </ul>
        </div>
        """,
        unsafe_allow_html=True,
    )
    enabled = telegram_is_configured()
    if enabled:
        st.success("Telegram is configured.")
        if st.button("Send Telegram Test Message", use_container_width=True):
            success = send_telegram("Ultimate DCA Bot dashboard test ping.")
            if success:
                st.success("Telegram test message sent.")
            else:
                st.error("Telegram test failed. Check auth settings.")
    else:
        st.warning("Telegram is not configured. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env.")


def render_live_market_trade_card(snapshot: PositionSnapshot) -> None:
    st.subheader("Live Market + Trade Card")

    for symbol in ("BTC/USDT", "ETH/USDT"):
        price = safe_float(market_data.get_price(symbol))
        change_pct = safe_float(market_data.fetch_24h_change(symbol))
        trend = infer_symbol_trend(symbol)
        if price > 0:
            st.metric(
                symbol,
                f"${price:,.2f}",
                f"{change_pct:+.2f}% | {trend}",
            )
        else:
            st.metric(symbol, "n/a", f"{trend}")

    render_live_charts("BTC/USDT")
    render_live_charts("ETH/USDT")

    open_positions = snapshot.open_positions if snapshot and snapshot.positions else {}
    if open_positions:
        position = next(iter(open_positions.values()))
        pnl = safe_float(getattr(position, "unrealized_pnl_usdt", 0.0))
        st.markdown("**Active Trade**")
        st.write(
            f"{position.symbol} | Entry {getattr(position, 'entry_price', 0):.2f} | "
            f"Current {getattr(position, 'current_price', 0):.2f} | PnL {pnl:+.2f} USDT"
        )
    else:
        st.markdown("**Active Trade**")
        st.write("Scanning market...")
        st.write("Waiting for setup...")


def render_live_charts(symbol: str = "BTC/USDT", timeframe: str = "1m", limit: int = 200) -> None:
    st.markdown(f"**{symbol} OHLCV**")
    try:
        ohlcv = market_data.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        if not ohlcv:
            st.info("Live chart unavailable: no OHLCV data")
            return

        df = pd.DataFrame(ohlcv, columns=["ts", "open", "high", "low", "close", "volume"])
        df["datetime"] = pd.to_datetime(df["ts"], unit="ms")
        fig = go.Figure()
        fig.add_trace(
            go.Candlestick(
                x=df["datetime"],
                open=df["open"],
                high=df["high"],
                low=df["low"],
                close=df["close"],
                name=symbol,
            )
        )
        fig.update_layout(margin=dict(l=0, r=0, t=20, b=0), height=260, template="plotly_dark")
        st.plotly_chart(fig, use_container_width=True)
    except Exception as exc:
        st.info(f"Chart error: {exc}")


def render_portfolio_panel(snapshot, performance, paper_report, trade_history_mgr, control) -> None:
    st.subheader("Portfolio Panel")
    try:
        equity = float(paper_report.get("final_equity_usdt", 1000.0) or 1000.0)
        daily_stats = trade_history_mgr.get_stats(time_filter="today")
        weekly_stats = trade_history_mgr.get_stats(time_filter="7d")
        daily_pnl = float(daily_stats.net_pnl_usdt if daily_stats else 0.0)
        weekly_pnl = float(weekly_stats.net_pnl_usdt if weekly_stats else 0.0)
        exposure = sum(
            getattr(pos, 'current_price', 0) * getattr(pos, 'remaining_quantity', 0)
            for pos in (snapshot.open_positions.values() if snapshot else [])
        )
        exposure_pct = (exposure / equity * 100) if equity > 0 else 0

        st.metric("Equity", f"${equity:.2f}")
        st.metric("Daily PnL", f"${daily_pnl:+.2f}")
        st.metric("Weekly PnL", f"${weekly_pnl:+.2f}")
        st.metric("Exposure %", f"{exposure_pct:.1f}%")

        open_count = len(snapshot.open_positions) if snapshot else 0
        st.text(f"Open Positions: {open_count}")

        enabled = bool(control.get("enabled", False))
        button_col1, button_col2 = st.columns(2)
        with button_col1:
            if enabled:
                if st.button("PAUSE BOT", use_container_width=True):
                    disable_bot()
                    st.rerun()
            else:
                if st.button("START BOT", use_container_width=True):
                    enable_bot()
                    st.rerun()
        with button_col2:
            if st.button("STOP BOT NOW", use_container_width=True):
                disable_bot()
                st.rerun()
    except Exception as e:
        st.error(f"Portfolio panel error: {str(e)}")


def render_trade_story_timeline(trade_history_mgr) -> None:
    st.markdown("**Trade Story Timeline**")
    try:
        trades = trade_history_mgr.load_filtered(time_filter="7d")
        if not trades:
            st.info("No recent trades. Waiting for market opportunity...")
            return
        for trade in sorted(trades, key=lambda t: t.timestamp, reverse=True)[:12]:
            entry_price = getattr(trade, 'entry_price', 'N/A')
            reasons = getattr(trade, 'entry_reason', []) or []
            reason = ", ".join(reasons[:2]) if reasons else "setup recorded"
            exit_price = getattr(trade, 'exit_price', 'N/A')
            pnl = getattr(trade, 'pnl_usdt', 0.0)
            stamp = str(getattr(trade, 'timestamp', ''))[:16].replace("T", " ")
            outcome = "WIN" if pnl >= 0 else "LOSS"
            st.markdown(
                f"- {stamp} | {getattr(trade, 'symbol', '')} | "
                f"Entry {entry_price} -> Exit {exit_price} | {outcome} {pnl:+.2f} USDT | Reason: {reason}"
            )
    except Exception as e:
        st.info(f"Trade history: {str(e) if str(e) else 'No trades recorded yet'}")


def render_intelligence_panel(health, control, journal_entries) -> None:
    st.markdown("**Intelligence Panel**")
    latest_entry = latest_payload(journal_entries, {"entry_decision", "entry_rejected"})
    blockers = list(latest_entry.get("blockers", []))
    warnings = list(latest_entry.get("warnings", []))
    risk_mode = str(latest_entry.get("recovery_mode") or "normal").upper()
    cooldown_seconds = int(latest_entry.get("remaining_seconds", 0) or 0)
    cooldown_minutes = round(cooldown_seconds / 60, 1) if cooldown_seconds > 0 else 0.0

    if not blockers:
        st.success("No hard blockers right now.")
    else:
        st.warning("Current blockers")
        for blocker in blockers[:6]:
            st.write(f"- {blocker}")

    st.write(f"- Risk mode: **{risk_mode}**")
    st.write(f"- Cooldown timer: **{cooldown_minutes} min**")
    st.write(f"- Health: **{health_status_label(health)}**")
    st.write(f"- Control mode: **{str(control.get('mode', 'paper')).upper()}**")
    st.write(f"- Enabled: **{'Yes' if control.get('enabled', False) else 'No'}**")

    risk_warnings = []
    if health.get("error_count", 0) >= 5:
        risk_warnings.append("Error count elevated")
    if health.get("reconnect_count", 0) >= 3:
        risk_warnings.append("Reconnect frequency elevated")
    if risk_mode in {"DEFENSIVE", "SURVIVAL", "PAUSED"}:
        risk_warnings.append(f"Recovery mode is {risk_mode}")
    risk_warnings.extend(warnings[:4])

    if risk_warnings:
        st.markdown("**Risk warnings**")
        for item in risk_warnings:
            st.write(f"- {item}")


def render_bottom_sections(trade_history_mgr, health, control, journal_entries) -> None:
    st.subheader("Trade Story + Intelligence")
    left_col, right_col = st.columns([1.2, 1], gap="large")
    with left_col:
        render_trade_story_timeline(trade_history_mgr)
    with right_col:
        render_intelligence_panel(health, control, journal_entries)


def safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def infer_symbol_trend(symbol: str) -> str:
    try:
        ohlcv = market_data.fetch_ohlcv(symbol, timeframe="15m", limit=80)
        if not ohlcv or len(ohlcv) < 50:
            return "Unknown"
        closes = pd.Series([row[4] for row in ohlcv], dtype="float64")
        ema_fast = closes.ewm(span=20).mean().iloc[-1]
        ema_slow = closes.ewm(span=50).mean().iloc[-1]
        if ema_fast > ema_slow:
            return "Bullish"
        if ema_fast < ema_slow:
            return "Bearish"
        return "Sideways"
    except Exception:
        return "Unknown"


def infer_btc_regime() -> str:
    return infer_symbol_trend("BTC/USDT")


def estimate_market_stress_score() -> int:
    try:
        btc_change = abs(safe_float(market_data.fetch_24h_change("BTC/USDT")))
        eth_change = abs(safe_float(market_data.fetch_24h_change("ETH/USDT")))
        score = int(min(100, (btc_change + eth_change) * 8))
        return score
    except Exception:
        return 0


def build_health_state(journal_entries) -> dict:
    log_text = tail_lines(LOG_FILE, limit=200).lower() if LOG_FILE.exists() else ""
    reconnect_count = log_text.count("connection restored")
    error_count = log_text.count("error")
    api_health = "Healthy"
    if error_count >= 5:
        api_health = "Degraded"
    if "authentication" in log_text:
        api_health = "Auth Issue"

    connection_events = [
        line
        for line in tail_lines(LOG_FILE, limit=80).splitlines()
        if "connection" in line.lower() or "testnet" in line.lower()
    ] if LOG_FILE.exists() else []

    return {
        "api_health": api_health,
        "reconnect_count": reconnect_count,
        "error_count": error_count,
        "last_connection_event": connection_events[-1] if connection_events else "No connection events yet",
    }



def health_status_label(health: dict) -> str:
    if health.get("api_health") == "Auth Issue" or health.get("error_count", 0) >= 10:
        return "Danger"
    if health.get("api_health") == "Degraded" or health.get("error_count", 0) >= 5:
        return "Warning"
    return "Healthy"


def health_status_class(health: dict) -> str:
    label = health_status_label(health)
    if label == "Danger":
        return "danger"
    if label == "Warning":
        return "warn"
    return "good"




def regime_class(regime: str) -> str:
    regime = regime.lower()
    if regime == "bullish":
        return "good"
    if regime in {"volatile", "bearish"}:
        return "danger"
    if regime == "sideways":
        return "warn"
    return "info"



def latest_payload(journal_entries, event_types: set[str]) -> dict:
    for entry in reversed(journal_entries):
        if entry.event_type in event_types:
            payload = entry.payload if isinstance(entry.payload, dict) else {}
            return {
                **payload,
                "symbol": entry.symbol,
                "status": entry.status,
                "score": entry.score,
                "regime": entry.regime,
                "recovery_mode": entry.recovery_mode,
                "risk_state": entry.risk_state,
                "market_stress_score": entry.market_stress_score,
            }
    return {}




def load_paper_report() -> dict:
    if not PAPER_REPORT_FILE.exists():
        return {}
    try:
        return json.loads(PAPER_REPORT_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}



def safe_load_positions():
    try:
        return PositionManager().load()
    except Exception as exc:
        st.error(f"Failed to load positions: {exc}")
        return PositionSnapshot(positions={}, updated_at=None)


def format_metric(value: float) -> str:
    if value == float("inf"):
        return "inf"
    return f"{value:.2f}"


def short_timestamp(value: object) -> str:
    if not value:
        return "Never"
    text = str(value)
    return text.replace("T", " ")[:19]


if __name__ == "__main__":
    main()

