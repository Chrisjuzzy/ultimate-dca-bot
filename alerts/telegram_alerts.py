import os

import requests
from dotenv import load_dotenv

from utils.logger import logger


def _get_telegram_settings() -> tuple[str, str]:
    load_dotenv()

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    return token, chat_id


def telegram_is_configured() -> bool:
    token, chat_id = _get_telegram_settings()
    return bool(token and chat_id)


def send_telegram(message: str, log_missing: bool = False) -> bool:
    token, chat_id = _get_telegram_settings()

    if not token or not chat_id:
        if log_missing:
            logger.warning("Telegram credentials are missing. Skipping Telegram notification.")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    try:
        response = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": message,
            },
            timeout=10,
        )
        if not response.ok:
            logger.warning(
                "Telegram notification failed: status=%s response=%s",
                response.status_code,
                response.text[:300],
            )
            return False

        logger.info("Telegram alert sent")
        return True
    except requests.RequestException as exc:
        logger.warning("Telegram notification failed: %s", exc.__class__.__name__)
        return False
