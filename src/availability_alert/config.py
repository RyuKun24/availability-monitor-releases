from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List

try:
    import winreg  # type: ignore
except Exception:  # pragma: no cover
    winreg = None


DEFAULT_URLS: List[str] = []


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_chat_id: str
    check_interval_seconds: int = 15
    monitor_start_time_local: str = ""
    alert_recheck_cooldown_seconds: int = 600
    request_timeout_seconds: int = 12
    max_concurrent_checks: int = 4
    state_file: str = ".state.json"
    log_level: str = "INFO"
    checker_engine: str = "auto"
    browser_headless: bool = True
    browser_timeout_seconds: int = 20
    browser_user_data_dir: str = ""
    browser_channel: str = "chrome"
    browser_cdp_url: str = ""
    expected_ship_zip: str = ""
    enforce_ship_zip: bool = False
    urls: List[str] = None
    url_titles: dict[str, str] = None
    url_max_prices: dict[str, float] = None
    url_auto_add_to_cart: dict[str, bool] = None
    url_auto_checkout: dict[str, bool] = None
    url_auto_paypal_payment: dict[str, bool] = None
    monitored_stores: List[str] = None


def _read_windows_user_env(name: str) -> str:
    if winreg is None:
        return ""

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
            raw, _ = winreg.QueryValueEx(key, name)
            return str(raw).strip()
    except OSError:
        return ""


def _resolve_env_ref(value: str, current_key: str = "") -> str:
    value = (value or "").strip()
    # Support .env indirection like TELEGRAM_BOT_TOKEN=$env:TELEGRAM_BOT_TOKEN
    if value.lower().startswith("$env:"):
        ref_name = value[5:].strip()
        if not ref_name:
            return ""

        # Avoid self-referencing loops by resolving from user environment on Windows.
        if ref_name.lower() == (current_key or "").lower():
            direct = _read_windows_user_env(ref_name)
            if direct:
                return direct
            return os.getenv(ref_name, "").strip()

        ref_value = os.getenv(ref_name, "").strip()
        if ref_value.lower().startswith("$env:"):
            direct = _read_windows_user_env(ref_name)
            if direct:
                return direct
        return ref_value
    return value


def _read_urls_from_env() -> List[str]:
    raw = os.getenv("TARGET_URLS", "").strip()
    if not raw:
        return DEFAULT_URLS
    urls = [item.strip() for item in raw.split(",") if item.strip()]
    return urls if urls else DEFAULT_URLS


def load_settings() -> Settings:
    token = _resolve_env_ref(os.getenv("TELEGRAM_BOT_TOKEN", ""), "TELEGRAM_BOT_TOKEN")
    chat_id = _resolve_env_ref(os.getenv("TELEGRAM_CHAT_ID", ""), "TELEGRAM_CHAT_ID")

    if not token:
        raise ValueError("Missing TELEGRAM_BOT_TOKEN in environment")
    if not chat_id:
        raise ValueError("Missing TELEGRAM_CHAT_ID in environment")

    interval = int(_resolve_env_ref(os.getenv("CHECK_INTERVAL_SECONDS", "15"), "CHECK_INTERVAL_SECONDS") or "15")
    alert_recheck_cooldown = int(
        _resolve_env_ref(
            os.getenv("ALERT_RECHECK_COOLDOWN_SECONDS", "600"),
            "ALERT_RECHECK_COOLDOWN_SECONDS",
        )
        or "600"
    )
    timeout = int(_resolve_env_ref(os.getenv("REQUEST_TIMEOUT_SECONDS", "12"), "REQUEST_TIMEOUT_SECONDS") or "12")
    state_file = _resolve_env_ref(os.getenv("STATE_FILE", ".state.json"), "STATE_FILE") or ".state.json"
    log_level = _resolve_env_ref(os.getenv("LOG_LEVEL", "INFO"), "LOG_LEVEL") or "INFO"
    checker_engine = (
        _resolve_env_ref(os.getenv("CHECKER_ENGINE", "auto"), "CHECKER_ENGINE") or "auto"
    ).lower()
    browser_headless = (
        _resolve_env_ref(os.getenv("BROWSER_HEADLESS", "true"), "BROWSER_HEADLESS") or "true"
    ).lower() != "false"
    browser_timeout_seconds = int(
        _resolve_env_ref(os.getenv("BROWSER_TIMEOUT_SECONDS", "20"), "BROWSER_TIMEOUT_SECONDS") or "20"
    )
    browser_user_data_dir = _resolve_env_ref(os.getenv("BROWSER_USER_DATA_DIR", ""), "BROWSER_USER_DATA_DIR")
    browser_channel = (_resolve_env_ref(os.getenv("BROWSER_CHANNEL", "chrome"), "BROWSER_CHANNEL") or "chrome").lower()
    browser_cdp_url = _resolve_env_ref(os.getenv("BROWSER_CDP_URL", ""), "BROWSER_CDP_URL")
    expected_ship_zip = _resolve_env_ref(os.getenv("EXPECTED_SHIP_ZIP", ""), "EXPECTED_SHIP_ZIP")
    enforce_ship_zip = (
        _resolve_env_ref(os.getenv("ENFORCE_SHIP_ZIP", "true"), "ENFORCE_SHIP_ZIP") or "true"
    ).lower() != "false"
    max_concurrent_checks = max(
        1, int(_resolve_env_ref(os.getenv("MAX_CONCURRENT_CHECKS", "4"), "MAX_CONCURRENT_CHECKS") or "4")
    )

    return Settings(
        telegram_bot_token=token,
        telegram_chat_id=chat_id,
        check_interval_seconds=interval,
        alert_recheck_cooldown_seconds=max(0, alert_recheck_cooldown),
        request_timeout_seconds=timeout,
        max_concurrent_checks=max_concurrent_checks,
        state_file=state_file,
        log_level=log_level,
        checker_engine=checker_engine,
        browser_headless=browser_headless,
        browser_timeout_seconds=browser_timeout_seconds,
        browser_user_data_dir=browser_user_data_dir,
        browser_channel=browser_channel,
        browser_cdp_url=browser_cdp_url,
        expected_ship_zip=expected_ship_zip,
        enforce_ship_zip=enforce_ship_zip,
        urls=_read_urls_from_env(),
    )
