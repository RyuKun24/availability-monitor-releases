from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import RLock
from datetime import datetime
from typing import Any

from .config import DEFAULT_URLS, Settings, load_settings


APP_SETTINGS_FILE = "app_settings.json"


@dataclass
class AppSettings:
    telegram_bot_token: str
    telegram_chat_id: str
    check_interval_seconds: int = 30
    monitor_start_time_local: str = ""
    alert_recheck_cooldown_seconds: int = 600
    request_timeout_seconds: int = 12
    max_concurrent_checks: int = 4
    state_file: str = ".state.json"
    log_level: str = "INFO"
    checker_engine: str = "browser"
    browser_headless: bool = False
    browser_timeout_seconds: int = 20
    browser_user_data_dir: str = ""
    browser_channel: str = "chrome"
    browser_cdp_url: str = ""
    expected_ship_zip: str = ""
    enforce_ship_zip: bool = True
    urls: list[str] = None
    url_titles: dict[str, str] = None
    url_max_prices: dict[str, float] = None
    url_auto_add_to_cart: dict[str, bool] = None
    url_auto_checkout: dict[str, bool] = None
    url_auto_paypal_payment: dict[str, bool] = None
    monitored_stores: list[str] = None

    def to_runtime_settings(self) -> Settings:
        normalized_urls = self._normalized_urls()
        return Settings(
            telegram_bot_token=self.telegram_bot_token.strip(),
            telegram_chat_id=self.telegram_chat_id.strip(),
            check_interval_seconds=max(1, int(self.check_interval_seconds)),
            monitor_start_time_local=self._normalized_monitor_start_time_local(),
            alert_recheck_cooldown_seconds=max(0, int(self.alert_recheck_cooldown_seconds)),
            request_timeout_seconds=max(1, int(self.request_timeout_seconds)),
            max_concurrent_checks=max(1, int(self.max_concurrent_checks)),
            state_file=(self.state_file or ".state.json").strip(),
            log_level=(self.log_level or "INFO").strip().upper(),
            checker_engine=(self.checker_engine or "browser").strip().lower(),
            browser_headless=bool(self.browser_headless),
            browser_timeout_seconds=max(5, int(self.browser_timeout_seconds)),
            browser_user_data_dir=(self.browser_user_data_dir or "").strip(),
            browser_channel=(self.browser_channel or "chrome").strip().lower(),
            browser_cdp_url=(self.browser_cdp_url or "").strip(),
            expected_ship_zip=(self.expected_ship_zip or "").strip(),
            enforce_ship_zip=bool(self.enforce_ship_zip),
            urls=normalized_urls,
            url_titles={u: t for u, t in self._normalized_url_titles().items() if u in normalized_urls},
            url_max_prices={u: p for u, p in self._normalized_url_max_prices().items() if u in normalized_urls},
            url_auto_add_to_cart={u: v for u, v in self._normalized_url_auto_add_to_cart().items() if u in normalized_urls},
            url_auto_checkout={u: v for u, v in self._normalized_url_auto_checkout().items() if u in normalized_urls},
            url_auto_paypal_payment={u: v for u, v in self._normalized_url_auto_paypal_payment().items() if u in normalized_urls},
            monitored_stores=self._normalized_monitored_stores(),
        )

    def _normalized_monitor_start_time_local(self) -> str:
        raw = str(self.monitor_start_time_local or "").strip()
        if not raw:
            return ""

        parts = raw.split(":")
        if len(parts) != 2:
            raise ValueError("monitor_start_time_local must use HH:MM format")

        hour_text, minute_text = parts
        if not hour_text.isdigit() or not minute_text.isdigit():
            raise ValueError("monitor_start_time_local must use HH:MM format")

        hour = int(hour_text)
        minute = int(minute_text)
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            raise ValueError("monitor_start_time_local must be a valid local time")

        return f"{hour:02d}:{minute:02d}"

    def _normalized_urls(self) -> list[str]:
        if self.urls is None:
            return list(DEFAULT_URLS)

        raw_urls = list(self.urls)
        urls = [u.strip() for u in raw_urls if isinstance(u, str) and u.strip()]
        return list(dict.fromkeys(urls))

    def _normalized_url_titles(self) -> dict[str, str]:
        raw_titles = self.url_titles if isinstance(self.url_titles, dict) else {}
        normalized_urls = set(self._normalized_urls())
        titles: dict[str, str] = {}
        for raw_url, raw_title in raw_titles.items():
            url = str(raw_url or "").strip()
            if not url or url not in normalized_urls:
                continue
            title = str(raw_title or "").strip()
            if title:
                titles[url] = title
        return titles

    def _normalized_url_max_prices(self) -> dict[str, float]:
        raw = self.url_max_prices if isinstance(self.url_max_prices, dict) else {}
        normalized_urls = set(self._normalized_urls())
        result: dict[str, float] = {}
        for raw_url, raw_price in raw.items():
            url = str(raw_url or "").strip()
            if not url or url not in normalized_urls:
                continue
            try:
                result[url] = float(raw_price)
            except (TypeError, ValueError):
                pass
        return result

    def _normalized_url_auto_add_to_cart(self) -> dict[str, bool]:
        raw = self.url_auto_add_to_cart if isinstance(self.url_auto_add_to_cart, dict) else {}
        normalized_urls = set(self._normalized_urls())
        result: dict[str, bool] = {}
        for raw_url, raw_flag in raw.items():
            url = str(raw_url or "").strip()
            if not url or url not in normalized_urls:
                continue
            result[url] = bool(raw_flag)
        return result

    def _normalized_url_auto_checkout(self) -> dict[str, bool]:
        raw = self.url_auto_checkout if isinstance(self.url_auto_checkout, dict) else {}
        normalized_urls = set(self._normalized_urls())
        result: dict[str, bool] = {}
        for raw_url, raw_flag in raw.items():
            url = str(raw_url or "").strip()
            if not url or url not in normalized_urls:
                continue
            result[url] = bool(raw_flag)
        return result

    def _normalized_url_auto_paypal_payment(self) -> dict[str, bool]:
        raw = self.url_auto_paypal_payment if isinstance(self.url_auto_paypal_payment, dict) else {}
        normalized_urls = set(self._normalized_urls())
        result: dict[str, bool] = {}
        for raw_url, raw_flag in raw.items():
            url = str(raw_url or "").strip()
            if not url or url not in normalized_urls:
                continue
            result[url] = bool(raw_flag)
        return result

    def _normalized_monitored_stores(self) -> list[str]:
        raw = self.monitored_stores
        default = ["amazon", "target", "walmart", "other"]
        if raw is None:
            return default

        allowed = {"amazon", "target", "walmart", "other"}
        normalized: list[str] = []
        for entry in raw:
            name = str(entry or "").strip().lower()
            if name in allowed and name not in normalized:
                normalized.append(name)
        return normalized or default


def _from_runtime_settings(runtime_settings: Settings) -> AppSettings:
    return AppSettings(
        telegram_bot_token=runtime_settings.telegram_bot_token,
        telegram_chat_id=runtime_settings.telegram_chat_id,
        check_interval_seconds=runtime_settings.check_interval_seconds,
        monitor_start_time_local=runtime_settings.monitor_start_time_local,
        alert_recheck_cooldown_seconds=runtime_settings.alert_recheck_cooldown_seconds,
        request_timeout_seconds=runtime_settings.request_timeout_seconds,
        max_concurrent_checks=runtime_settings.max_concurrent_checks,
        state_file=runtime_settings.state_file,
        log_level=runtime_settings.log_level,
        checker_engine=runtime_settings.checker_engine,
        browser_headless=runtime_settings.browser_headless,
        browser_timeout_seconds=runtime_settings.browser_timeout_seconds,
        browser_user_data_dir=runtime_settings.browser_user_data_dir,
        browser_channel=runtime_settings.browser_channel,
        browser_cdp_url=runtime_settings.browser_cdp_url,
        expected_ship_zip=runtime_settings.expected_ship_zip,
        enforce_ship_zip=runtime_settings.enforce_ship_zip,
        urls=list(runtime_settings.urls or []),
        url_titles=dict(runtime_settings.url_titles or {}),
        url_max_prices=dict(runtime_settings.url_max_prices or {}),
        url_auto_add_to_cart=dict(runtime_settings.url_auto_add_to_cart or {}),
        url_auto_checkout=dict(runtime_settings.url_auto_checkout or {}),
        url_auto_paypal_payment=dict(runtime_settings.url_auto_paypal_payment or {}),
        monitored_stores=list(runtime_settings.monitored_stores or []),
    )


def _default_settings() -> AppSettings:
    # Try to preserve current behavior by migrating from .env on first run.
    try:
        runtime = load_settings()
        return _from_runtime_settings(runtime)
    except Exception:
        return AppSettings(
            telegram_bot_token="",
            telegram_chat_id="",
            urls=list(DEFAULT_URLS),
        )


def _hydrate_missing_from_env(settings: AppSettings) -> AppSettings:
    # Backfill missing values from environment-based config without overriding saved values.
    try:
        env_settings = load_settings()
    except Exception:
        return settings

    updated = AppSettings(**asdict(settings))
    changed = False

    def fill_if_missing(attr: str, value: Any) -> None:
        nonlocal changed
        current = getattr(updated, attr)
        if isinstance(current, str):
            if not current.strip() and isinstance(value, str) and value.strip():
                setattr(updated, attr, value)
                changed = True
            return
        if current is None and value is not None:
            setattr(updated, attr, value)
            changed = True

    fill_if_missing("telegram_bot_token", env_settings.telegram_bot_token)
    fill_if_missing("telegram_chat_id", env_settings.telegram_chat_id)
    fill_if_missing("browser_cdp_url", env_settings.browser_cdp_url)
    fill_if_missing("browser_user_data_dir", env_settings.browser_user_data_dir)
    fill_if_missing("browser_channel", env_settings.browser_channel)
    fill_if_missing("expected_ship_zip", env_settings.expected_ship_zip)

    if updated.urls is None:
        updated.urls = list(env_settings.urls or DEFAULT_URLS)
        changed = True

    if not changed:
        return settings
    return updated


def _clean_payload(payload: dict[str, Any]) -> dict[str, Any]:
    known_keys = {
        "telegram_bot_token",
        "telegram_chat_id",
        "check_interval_seconds",
        "monitor_start_time_local",
        "alert_recheck_cooldown_seconds",
        "request_timeout_seconds",
        "max_concurrent_checks",
        "state_file",
        "log_level",
        "checker_engine",
        "browser_headless",
        "browser_timeout_seconds",
        "browser_user_data_dir",
        "browser_channel",
        "browser_cdp_url",
        "expected_ship_zip",
        "enforce_ship_zip",
        "urls",
        "url_titles",
        "url_max_prices",
        "url_auto_add_to_cart",
        "url_auto_checkout",
        "url_auto_paypal_payment",
        "monitored_stores",
    }
    # Saved files contain null for unset optional fields; treat them as absent.
    cleaned = {k: v for k, v in payload.items() if k in known_keys and v is not None}

    if "urls" in cleaned and not isinstance(cleaned["urls"], list):
        raise ValueError("urls must be a list of strings")
    if "url_titles" in cleaned and not isinstance(cleaned["url_titles"], dict):
        raise ValueError("url_titles must be an object map of url -> title")
    if "url_max_prices" in cleaned:
        if not isinstance(cleaned["url_max_prices"], dict):
            raise ValueError("url_max_prices must be an object map of url -> number")
        cleaned["url_max_prices"] = {str(k): float(v) for k, v in cleaned["url_max_prices"].items()}
    if "url_auto_add_to_cart" in cleaned:
        if not isinstance(cleaned["url_auto_add_to_cart"], dict):
            raise ValueError("url_auto_add_to_cart must be an object map of url -> boolean")
        cleaned["url_auto_add_to_cart"] = {str(k): bool(v) for k, v in cleaned["url_auto_add_to_cart"].items()}
    if "url_auto_checkout" in cleaned:
        if not isinstance(cleaned["url_auto_checkout"], dict):
            raise ValueError("url_auto_checkout must be an object map of url -> boolean")
        cleaned["url_auto_checkout"] = {str(k): bool(v) for k, v in cleaned["url_auto_checkout"].items()}
    if "url_auto_paypal_payment" in cleaned:
        if not isinstance(cleaned["url_auto_paypal_payment"], dict):
            raise ValueError("url_auto_paypal_payment must be an object map of url -> boolean")
        cleaned["url_auto_paypal_payment"] = {str(k): bool(v) for k, v in cleaned["url_auto_paypal_payment"].items()}
    if "monitored_stores" in cleaned:
        if not isinstance(cleaned["monitored_stores"], list):
            raise ValueError("monitored_stores must be a list")
        cleaned["monitored_stores"] = [str(v).strip().lower() for v in cleaned["monitored_stores"]]

    for key in ("check_interval_seconds", "alert_recheck_cooldown_seconds", "request_timeout_seconds", "max_concurrent_checks", "browser_timeout_seconds"):
        if key in cleaned:
            cleaned[key] = int(cleaned[key])

    for key in ("browser_headless", "enforce_ship_zip"):
        if key in cleaned:
            cleaned[key] = bool(cleaned[key])

    if "monitor_start_time_local" in cleaned:
        cleaned["monitor_start_time_local"] = str(cleaned["monitor_start_time_local"] or "").strip()

    return cleaned


class AppSettingsStore:
    def __init__(self, path: str = APP_SETTINGS_FILE) -> None:
        self._path = Path(path)
        self._lock = RLock()
        self._settings = self._load_or_initialize()

    def _load_or_initialize(self) -> AppSettings:
        if self._path.exists():
            try:
                payload = json.loads(self._path.read_text(encoding="utf-8-sig"))
                if isinstance(payload, dict):
                    cleaned = _clean_payload(payload)
                    cleaned.setdefault("telegram_bot_token", "")
                    cleaned.setdefault("telegram_chat_id", "")
                    loaded = AppSettings(**cleaned)
                    hydrated = _hydrate_missing_from_env(loaded)
                    if asdict(hydrated) != asdict(loaded):
                        self._write(hydrated)
                    return hydrated
            except Exception as exc:
                print(f"Invalid {self._path}: {exc!r}", file=sys.stderr, flush=True)
                # Preserve unreadable settings for manual recovery instead of silently overwriting.
                try:
                    backup_name = f"{self._path.name}.invalid-{datetime.now().strftime('%Y%m%d-%H%M%S')}.bak"
                    backup_path = self._path.with_name(backup_name)
                    backup_path.write_text(self._path.read_text(encoding="utf-8"), encoding="utf-8")
                except Exception:
                    pass
                settings = _default_settings()
                # Do not overwrite the existing settings file on parse failure.
                return settings

        settings = _default_settings()
        self._write(settings)
        return settings

    def _write(self, settings: AppSettings) -> None:
        self._path.write_text(
            json.dumps(asdict(settings), indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def get(self) -> AppSettings:
        with self._lock:
            payload = asdict(self._settings)
            return AppSettings(**payload)

    def update_partial(self, updates: dict[str, Any]) -> AppSettings:
        with self._lock:
            cleaned = _clean_payload(updates)
            payload = asdict(self._settings)
            payload.update(cleaned)
            updated = AppSettings(**payload)
            if updated.check_interval_seconds < 1:
                raise ValueError("check_interval_seconds must be >= 1")
            if updated.alert_recheck_cooldown_seconds < 0:
                raise ValueError("alert_recheck_cooldown_seconds must be >= 0")
            if updated.request_timeout_seconds < 1:
                raise ValueError("request_timeout_seconds must be >= 1")
            if updated.max_concurrent_checks < 1:
                raise ValueError("max_concurrent_checks must be >= 1")
            if not updated.telegram_bot_token.strip():
                raise ValueError("telegram_bot_token is required")
            if not updated.telegram_chat_id.strip():
                raise ValueError("telegram_chat_id is required")

            self._settings = updated
            self._write(self._settings)
            return self.get()

    def add_url(self, url: str, title: str = "") -> AppSettings:
        cleaned_url = (url or "").strip()
        if not cleaned_url:
            raise ValueError("url is required")
        cleaned_title = (title or "").strip()

        with self._lock:
            settings = self.get()
            urls = settings.urls or []
            if cleaned_url not in urls:
                urls.append(cleaned_url)
            settings.urls = urls
            titles = dict(settings.url_titles or {})
            if cleaned_title:
                titles[cleaned_url] = cleaned_title
            else:
                titles.pop(cleaned_url, None)
            settings.url_titles = titles
            self._settings = settings
            self._write(self._settings)
            return self.get()

    def remove_url(self, url: str) -> AppSettings:
        cleaned_url = (url or "").strip()
        with self._lock:
            settings = self.get()
            settings.urls = [u for u in (settings.urls or []) if u != cleaned_url]
            titles = dict(settings.url_titles or {})
            titles.pop(cleaned_url, None)
            settings.url_titles = titles
            max_prices = dict(settings.url_max_prices or {})
            max_prices.pop(cleaned_url, None)
            settings.url_max_prices = max_prices
            auto_add = dict(settings.url_auto_add_to_cart or {})
            auto_add.pop(cleaned_url, None)
            settings.url_auto_add_to_cart = auto_add
            auto_checkout = dict(settings.url_auto_checkout or {})
            auto_checkout.pop(cleaned_url, None)
            settings.url_auto_checkout = auto_checkout
            auto_paypal = dict(settings.url_auto_paypal_payment or {})
            auto_paypal.pop(cleaned_url, None)
            settings.url_auto_paypal_payment = auto_paypal
            self._settings = settings
            self._write(self._settings)
            return self.get()

    def update_url_title(self, url: str, title: str) -> AppSettings:
        cleaned_url = (url or "").strip()
        if not cleaned_url:
            raise ValueError("url is required")

        with self._lock:
            settings = self.get()
            urls = settings.urls or []
            if cleaned_url not in urls:
                raise ValueError("url not found")

            titles = dict(settings.url_titles or {})
            cleaned_title = (title or "").strip()
            if cleaned_title:
                titles[cleaned_url] = cleaned_title
            else:
                # Empty custom title means fallback to scraped/persisted title.
                titles.pop(cleaned_url, None)

            settings.url_titles = titles
            self._settings = settings
            self._write(self._settings)
            return self.get()

    def update_url_cart_config(
        self,
        url: str,
        auto_add_to_cart: bool,
        max_price: float | None,
        auto_checkout: bool = False,
        auto_paypal_payment: bool = False,
    ) -> AppSettings:
        cleaned_url = (url or "").strip()
        if not cleaned_url:
            raise ValueError("url is required")

        with self._lock:
            settings = self.get()
            if cleaned_url not in (settings.urls or []):
                raise ValueError("url not found")

            auto_add = dict(settings.url_auto_add_to_cart or {})
            if auto_add_to_cart:
                auto_add[cleaned_url] = True
            else:
                auto_add.pop(cleaned_url, None)
            settings.url_auto_add_to_cart = auto_add

            auto_checkout_map = dict(settings.url_auto_checkout or {})
            if auto_add_to_cart and auto_checkout:
                auto_checkout_map[cleaned_url] = True
            else:
                auto_checkout_map.pop(cleaned_url, None)
            settings.url_auto_checkout = auto_checkout_map

            auto_paypal_map = dict(settings.url_auto_paypal_payment or {})
            if auto_add_to_cart and auto_checkout and auto_paypal_payment:
                auto_paypal_map[cleaned_url] = True
            else:
                auto_paypal_map.pop(cleaned_url, None)
            settings.url_auto_paypal_payment = auto_paypal_map

            max_prices = dict(settings.url_max_prices or {})
            if max_price is not None:
                max_prices[cleaned_url] = float(max_price)
            else:
                max_prices.pop(cleaned_url, None)
            settings.url_max_prices = max_prices

            self._settings = settings
            self._write(self._settings)
            return self.get()
