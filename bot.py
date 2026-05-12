import os
import time

import ccxt
from dotenv import load_dotenv

from alerts.telegram_alerts import send_telegram, telegram_is_configured
from core.runtime_control import load_control
from database.database import initialize_database
from utils.helpers import env_to_bool
from utils.logger import logger
from utils.state import get_open_positions_count, load_positions_state


POLL_INTERVAL_SECONDS = 60
PAUSE_SLEEP_SECONDS = 30
PAUSE_HEARTBEAT_SECONDS = 300
NETWORK_RETRY_DELAY_SECONDS = 30
AUTH_RETRY_DELAY_SECONDS = 300
EXCHANGE_RETRY_DELAY_SECONDS = 60
ERROR_ALERT_COOLDOWN_SECONDS = 900


def get_required_setting(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def build_exchange() -> ccxt.Exchange:
    exchange = ccxt.binance(
        {
            "apiKey": get_required_setting("BINANCE_API_KEY"),
            "secret": get_required_setting("BINANCE_API_SECRET"),
            "enableRateLimit": True,
            "options": {
                "adjustForTimeDifference": True,
                "recvWindow": 10000,
            },
        }
    )

    if env_to_bool(os.getenv("TESTNET"), default=False):
        exchange.set_sandbox_mode(True)
        logger.info("Running in TESTNET mode")

    return exchange


def maybe_send_error_alert(message: str, last_error_alert_at: float) -> float:
    now = time.monotonic()
    if telegram_is_configured() and now - last_error_alert_at >= ERROR_ALERT_COOLDOWN_SECONDS:
        send_telegram(message)
        return now
    return last_error_alert_at


def main() -> None:
    load_dotenv()

    logger.info("Starting Ultimate DCA Bot")
    initialize_database()
    positions_state = load_positions_state()
    positions_count = get_open_positions_count(positions_state)
    logger.info("Loaded %s persisted positions", positions_count)

    try:
        exchange = build_exchange()
    except ValueError as exc:
        logger.error("%s", exc)
        logger.error("Add your Binance Testnet keys to .env before running the bot.")
        return

    mode_label = "TESTNET" if env_to_bool(os.getenv("TESTNET"), default=False) else "LIVE"
    startup_alert_sent = False
    has_connected_once = False
    connection_active = False
    last_error_alert_at = 0.0
    last_pause_log_at = 0.0
    pause_logged = False
    last_restart_nonce = int(load_control().get("restart_nonce", 0) or 0)

    while True:
        try:
            control = load_control()
            current_restart_nonce = int(control.get("restart_nonce", 0) or 0)
            if current_restart_nonce != last_restart_nonce:
                logger.info("Runtime restart requested from dashboard control")
                exchange = build_exchange()
                positions_state = load_positions_state()
                positions_count = get_open_positions_count(positions_state)
                logger.info("Reloaded %s persisted positions after runtime restart", positions_count)
                has_connected_once = False
                connection_active = False
                startup_alert_sent = False
                last_restart_nonce = current_restart_nonce
                if telegram_is_configured():
                    send_telegram("Ultimate DCA Bot runtime restart requested")

            if not control.get("enabled", False):
                now = time.monotonic()
                if not pause_logged or now - last_pause_log_at >= PAUSE_HEARTBEAT_SECONDS:
                    logger.info("Bot paused from dashboard control")
                    last_pause_log_at = now
                    pause_logged = True
                time.sleep(PAUSE_SLEEP_SECONDS)
                continue

            if pause_logged:
                logger.info("Bot resumed from dashboard control")
                pause_logged = False

            balance = exchange.fetch_balance()
            usdt = balance["total"].get("USDT", 0)

            if not has_connected_once:
                logger.info("Connected to Binance %s successfully", mode_label)
                has_connected_once = True
            elif not connection_active:
                logger.info("Connection to Binance %s restored", mode_label)

            connection_active = True

            if not startup_alert_sent:
                send_telegram(
                    "\n".join(
                        [
                            "Ultimate DCA Bot Started",
                            f"Mode: {mode_label}",
                            f"Persisted positions: {positions_count}",
                            f"USDT Balance: {usdt}",
                        ]
                    ),
                    log_missing=True,
                )
                startup_alert_sent = True

            logger.info("USDT Balance: %s", usdt)
            time.sleep(POLL_INTERVAL_SECONDS)
        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
            if telegram_is_configured():
                send_telegram("Ultimate DCA Bot Stopped")
            break
        except ccxt.AuthenticationError as exc:
            connection_active = False
            logger.error("Authentication failed for Binance %s: %s", mode_label, exc)
            last_error_alert_at = maybe_send_error_alert(
                f"Ultimate DCA Bot authentication error: {exc}",
                last_error_alert_at,
            )
            logger.info(
                "Check Binance API credentials and retrying in %s seconds...",
                AUTH_RETRY_DELAY_SECONDS,
            )
            time.sleep(AUTH_RETRY_DELAY_SECONDS)
        except ccxt.NetworkError as exc:
            if connection_active:
                logger.warning("Connection to Binance %s lost: %s", mode_label, exc)
            else:
                logger.warning("Network error while connecting to Binance %s: %s", mode_label, exc)
            connection_active = False
            last_error_alert_at = maybe_send_error_alert(
                f"Ultimate DCA Bot network error: {exc}",
                last_error_alert_at,
            )
            logger.info("Retrying in %s seconds...", NETWORK_RETRY_DELAY_SECONDS)
            time.sleep(NETWORK_RETRY_DELAY_SECONDS)
        except ccxt.ExchangeError as exc:
            connection_active = False
            logger.error("Exchange error from Binance %s: %s", mode_label, exc)
            last_error_alert_at = maybe_send_error_alert(
                f"Ultimate DCA Bot exchange error: {exc}",
                last_error_alert_at,
            )
            logger.info("Retrying in %s seconds...", EXCHANGE_RETRY_DELAY_SECONDS)
            time.sleep(EXCHANGE_RETRY_DELAY_SECONDS)
        except Exception as exc:
            connection_active = False
            logger.error("Unexpected bot error: %s", exc)
            last_error_alert_at = maybe_send_error_alert(
                f"Ultimate DCA Bot unexpected error: {exc}",
                last_error_alert_at,
            )
            logger.info("Retrying in %s seconds...", EXCHANGE_RETRY_DELAY_SECONDS)
            time.sleep(EXCHANGE_RETRY_DELAY_SECONDS)


if __name__ == "__main__":
    main()
