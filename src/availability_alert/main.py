from __future__ import annotations

import logging
import time
from datetime import datetime

from dotenv import load_dotenv

from .app_settings import APP_SETTINGS_FILE, AppSettingsStore
from .browser_checker import (
    BrowserCheckerConfig,
    BrowserCheckerUnavailableError,
    BrowserProductChecker,
)
from .checker import ProductChecker
from .state import load_state, save_state
from .telegram_client import TelegramClient


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def format_alert_message(title: str, url: str) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return (
        "Product is now available for Add to Cart.\n"
        f"Time: {now}\n"
        f"Title: {title}\n"
        f"URL: {url}"
    )


def format_telegram_test_message() -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return (
        "Telegram connectivity test succeeded.\n"
        f"Time: {now}\n"
        "Source: availability-change-alert-sender"
    )


def run(test_telegram_only: bool = False) -> None:
    # Keep dotenv support for first-run hydration into app_settings.json.
    load_dotenv()
    store = AppSettingsStore(APP_SETTINGS_FILE)
    settings = store.get().to_runtime_settings()
    setup_logging(settings.log_level)

    telegram = TelegramClient(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
        timeout=settings.request_timeout_seconds,
    )

    if test_telegram_only:
        telegram.send_message(format_telegram_test_message())
        logging.info("Telegram test message sent successfully")
        return

    checker = None
    browser_checker = None

    if settings.checker_engine in {"auto", "browser"}:
        try:
            browser_checker = BrowserProductChecker(
                BrowserCheckerConfig(
                    timeout_seconds=settings.browser_timeout_seconds,
                    headless=settings.browser_headless,
                    user_data_dir=settings.browser_user_data_dir,
                    browser_channel=settings.browser_channel,
                    browser_cdp_url=settings.browser_cdp_url,
                    expected_ship_zip=settings.expected_ship_zip,
                    enforce_ship_zip=settings.enforce_ship_zip,
                )
            )
            checker = browser_checker
            logging.info("Using browser checker engine")
        except BrowserCheckerUnavailableError as exc:
            if settings.checker_engine == "browser":
                raise
            logging.warning("Browser checker unavailable, falling back to requests: %s", exc)

    if checker is None:
        checker = ProductChecker(timeout_seconds=settings.request_timeout_seconds)
        logging.info("Using requests checker engine")

    state = load_state(settings.state_file)
    next_check_at = {url: 0.0 for url in settings.urls}
    logging.info("Loaded state for %d URLs", len(state))
    logging.info("Monitoring %d URLs", len(settings.urls))
    logging.info(
        "URLs with availability alerts are retried after %d seconds",
        settings.alert_recheck_cooldown_seconds,
    )

    try:
        while True:
            now = time.time()
            due_urls = [url for url in settings.urls if next_check_at.get(url, 0.0) <= now]

            if not due_urls:
                earliest_retry = min(next_check_at.values()) if next_check_at else now
                sleep_seconds = max(1, int(earliest_retry - now))
                time.sleep(min(settings.check_interval_seconds, sleep_seconds))
                continue

            for url in due_urls:
                try:
                    status = checker.check(url)
                    previous = state.get(url)

                    logging.info(
                        "Checked: %s | available=%s | reason=%s",
                        status.title,
                        status.available,
                        status.reason,
                    )

                    should_alert = status.available is True and previous is not True
                    if should_alert:
                        message = format_alert_message(status.title, status.url)
                        telegram.send_message(message)
                        if previous is False:
                            logging.warning("Alert sent for transition: %s", status.title)
                        elif previous is None:
                            logging.warning("Alert sent for initial available state: %s", status.title)
                        else:
                            logging.warning("Alert sent for available state: %s", status.title)

                        next_check_at[url] = time.time() + settings.alert_recheck_cooldown_seconds
                        logging.info(
                            "Skipping URL for %d seconds before recheck: %s",
                            settings.alert_recheck_cooldown_seconds,
                            url,
                        )
                    else:
                        next_check_at[url] = time.time()

                    state[url] = status.available
                    save_state(settings.state_file, state)

                except Exception as exc:
                    logging.exception("Check failed for URL %s: %s", url, exc)
                    next_check_at[url] = time.time()

            time.sleep(settings.check_interval_seconds)
    finally:
        if browser_checker is not None:
            browser_checker.close()


if __name__ == "__main__":
    run()
