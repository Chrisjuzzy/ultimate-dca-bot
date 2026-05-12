import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from analytics.performance import build_performance_report
from analytics.trade_history import TradeHistoryManager, TimeFilter
from analytics.trade_journal import TradeJournal
from config import REFRESH_SECONDS
from core.runtime_control import disable_bot, enable_bot, load_control, request_restart, set_mode
from portfolio.positions import PositionManager, PositionSnapshot
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
            grid-template-columns: repeat(7, minmax(120px, 1fr));
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
    inject_styles()
    snapshot = safe_load_positions()
    journal = TradeJournal()
    journal_entries = journal.load(limit=200)
    performance = build_performance_report(snapshot, starting_equity_usdt=0)
    health = build_health_state(journal_entries)
    control = load_control()
    paper_report = load_paper_report()
    trade_history_mgr = TradeHistoryManager(TRADE_HISTORY_FILE)

    render_header()
    render_mode_banner(control)
    render_status_bar(control, health, paper_report, journal_entries, snapshot, trade_history_mgr)

    # New layout structure
    col1, col2 = st.columns([1, 1])

    with col1:
        render_live_market_trade_card()
        render_trade_story_timeline(trade_history_mgr)

    with col2:
        render_portfolio_panel(snapshot, performance, paper_report)
        render_intelligence_panel(health, control)


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
    performance = paper_report.get("performance", {}) if paper_report else {}
    equity = float(paper_report.get("final_equity_usdt", STARTING_EQUITY_USDT) or STARTING_EQUITY_USDT)
    drawdown = float(paper_report.get("max_equity_drawdown_percent", 0.0) or 0.0)
    win_rate = float(performance.get("win_rate_percent", 0.0) or 0.0)
    regime = latest_entry.get("regime") or "unknown"

    # Get stats from trade history
    trades = trade_history_mgr.load_filtered(time_filter="today")
    today_trades = len(trades)
    today_pnl = sum(t.pnl_usdt for t in trades) if trades else 0.0

    cards = [
        ("Bot Status", "Running" if enabled else "Paused", "good" if enabled else "warn"),
        ("Health", health_status_label(health), health_status_class(health)),
        ("Trades Today", str(today_trades), "info"),
        ("Daily PnL", f"{today_pnl:+.2f} USDT", "good" if today_pnl >= 0 else "danger"),
        ("Drawdown", f"{drawdown:.2f}%", "good" if drawdown < 3 else "warn"),
        ("Win Rate", f"{win_rate:.2f}%", "good" if 55 <= win_rate <= 75 else "warn"),
        ("Regime", str(regime).upper(), regime_class(str(regime))),
    ]
    html = '<div class="status-grid">'
    for label, value, klass in cards:
        html += f"""
        <div class="status-card {klass}">
            <span>{label}</span>
            <strong>{value}</strong>
        </div>
        """
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)


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


def render_live_market_trade_card() -> None:
    st.subheader("📊 Live Market + Trade Card")
    # Placeholder for BTC & ETH data
    st.metric("BTC Price", "$30,000", "+2.5%")
    st.metric("ETH Price", "$2,000", "+1.8%")
    st.text("Trend: Bullish")
    st.text("Active Trade: None")
    st.text("Scanning market...")


def render_portfolio_panel(snapshot, performance, paper_report) -> None:
    st.subheader("💼 Portfolio Panel")
    st.metric("Equity", "$10,000")
    st.metric("Daily PnL", "+$500")
    st.metric("Weekly PnL", "+$1,200")
    st.metric("Exposure %", "50%")
    st.text("Open Positions: 3")
    if st.button("STOP BOT NOW", use_container_width=True):
        disable_bot()
        st.rerun()


def render_trade_story_timeline(trade_history_mgr) -> None:
    st.subheader("📜 Trade Story Timeline")
    trades = trade_history_mgr.load_filtered(time_filter="today")
    for trade in trades:
        st.text(f"Entry: {trade.entry_price}, Reason: {trade.reason}, Exit: {trade.exit_price}, Profit: {trade.pnl_usdt}")


def render_intelligence_panel(health, control) -> None:
    st.subheader("⚠️ Intelligence Panel")
    st.text("Why no trade: Cooldown active")
    st.text("Current blockers: High volatility")
    st.text("Risk warnings: Elevated")
    st.text("Cooldown timer: 5 minutes")


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
