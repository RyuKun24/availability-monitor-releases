from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, time as dt_time
from typing import Any

from .app_settings import AppSettingsStore
from .browser_checker import (
    BrowserCheckerConfig,
    BrowserCheckerUnavailableError,
    BrowserProductChecker,
    CartActionResult,
)
from .checker import ProductChecker
from .state import load_state, save_state
from .telegram_client import TelegramClient


@dataclass
class UrlRuntimeStatus:
    url: str
    title: str = ""
    available: bool | None = None
    reason: str = "pending"
    last_checked_at: str = ""
    last_alert_at: str = ""
    next_check_at_epoch: float = 0.0
    error: str = ""
    last_price: str = ""
    last_cart_added_at: str = ""
    cart_add_error: str = ""
    last_checkout_stage: str = ""
    payment_completed: bool = False


class MonitorService:
    def __init__(self, store: AppSettingsStore) -> None:
        self._store = store
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        self._runtime: dict[str, UrlRuntimeStatus] = {}
        self._next_check_at: dict[str, float] = {}
        self._state: dict[str, dict[str, Any]] = {}
        self._cart_added_for_cycle: dict[str, bool] = {}
        self._last_error = ""
        self._waiting_for_start_time = False
        self._scheduled_start_time_local = ""
        self._start_gate_pending = False

    def start(self) -> None:
        with self._lock:
            if self.is_running():
                return
            settings = self._store.get().to_runtime_settings()
            self._state = load_state(settings.state_file)
            self._sync_urls(settings.urls, settings.url_titles or {})
            self._last_error = ""
            self._scheduled_start_time_local = settings.monitor_start_time_local
            self._start_gate_pending = bool(self._scheduled_start_time_local)
            self._waiting_for_start_time = self._start_gate_pending and self._is_waiting_for_start_time(
                self._scheduled_start_time_local,
            )
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run, name="monitor-service", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._stop_event.set()
            thread = self._thread
        if thread is not None:
            thread.join(timeout=10)
        with self._lock:
            self._thread = None
            self._waiting_for_start_time = False
            self._scheduled_start_time_local = ""
            self._start_gate_pending = False

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            # Keep runtime entries aligned with persisted settings so the dashboard
            # can render links even when the monitor is stopped.
            settings = self._store.get().to_runtime_settings()
            self._sync_urls(settings.urls, settings.url_titles or {})

            entries = [asdict(self._runtime[url]) for url in sorted(self._runtime)]
            now_epoch = time.time()
            for entry in entries:
                next_epoch = float(entry.get("next_check_at_epoch") or 0.0)
                entry["next_check_in_seconds"] = max(0, int(next_epoch - now_epoch))
                entry["store"] = self._store_for_url(str(entry.get("url") or ""))

            return {
                "running": self.is_running(),
                "waiting_for_start_time": self._waiting_for_start_time,
                "scheduled_start_time_local": self._scheduled_start_time_local,
                "last_error": self._last_error,
                "urls": entries,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }

    def _sync_urls(self, urls: list[str], url_titles: dict[str, str]) -> None:
        now = time.time()
        keep = set(urls)

        for removed in set(self._runtime.keys()) - keep:
            self._runtime.pop(removed, None)
            self._next_check_at.pop(removed, None)
            self._state.pop(removed, None)
            self._cart_added_for_cycle.pop(removed, None)

        for url in urls:
            entry = self._runtime.setdefault(url, UrlRuntimeStatus(url=url))
            persisted = self._state.get(url) or {}
            configured_title = str((url_titles or {}).get(url) or "").strip()

            if configured_title:
                entry.title = configured_title
            elif not entry.title:
                entry.title = str(persisted.get("title") or "")

            if entry.available is None and "available" in persisted:
                entry.available = bool(persisted.get("available"))

            if not entry.last_checked_at:
                entry.last_checked_at = str(persisted.get("last_checked_at") or "")

            if entry.reason == "pending" and entry.last_checked_at:
                entry.reason = "loaded_from_state"

            self._next_check_at.setdefault(url, now)
            self._cart_added_for_cycle.setdefault(url, False)

    def _run(self) -> None:
        checker = None
        browser_checker = None
        state_file = ".state.json"

        try:
            settings = self._store.get().to_runtime_settings()
            state_file = settings.state_file

            telegram = TelegramClient(
                bot_token=settings.telegram_bot_token,
                chat_id=settings.telegram_chat_id,
                timeout=settings.request_timeout_seconds,
            )

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
                except BrowserCheckerUnavailableError:
                    if settings.checker_engine == "browser":
                        raise

            if checker is None:
                checker = ProductChecker(timeout_seconds=settings.request_timeout_seconds)

            while not self._stop_event.is_set():
                settings = self._store.get().to_runtime_settings()
                with self._lock:
                    self._sync_urls(settings.urls, settings.url_titles or {})
                    self._scheduled_start_time_local = settings.monitor_start_time_local

                with self._lock:
                    start_gate_pending = self._start_gate_pending

                if start_gate_pending:
                    wait_seconds = self._wait_seconds_until_start(settings.monitor_start_time_local)
                    if wait_seconds > 0:
                        with self._lock:
                            self._waiting_for_start_time = True
                        self._stop_event.wait(timeout=min(wait_seconds, 30.0))
                        continue

                    with self._lock:
                        self._waiting_for_start_time = False
                        self._start_gate_pending = False

                now = time.time()
                active_urls = [
                    url for url in settings.urls
                    if self._is_store_enabled(url, settings.monitored_stores)
                ]
                due_urls = [url for url in active_urls if self._next_check_at.get(url, 0.0) <= now]

                if not due_urls:
                    sleep_seconds = max(1, min(settings.check_interval_seconds, 2))
                    self._stop_event.wait(timeout=sleep_seconds)
                    continue

                for url in due_urls:
                    if self._stop_event.is_set():
                        break

                    checked_at = datetime.now().isoformat(timespec="seconds")
                    try:
                        status = checker.check(url)
                        configured_title = str((settings.url_titles or {}).get(url) or "").strip()
                        previous = (self._state.get(url) or {}).get("available")
                        should_alert = status.available is True and previous is not True

                        alert_time = ""
                        next_check = time.time()
                        cart_result = None
                        price_str = f"${status.price:.2f}" if status.price is not None else ""
                        url_auto_add = settings.url_auto_add_to_cart or {}
                        url_auto_checkout = settings.url_auto_checkout or {}
                        url_auto_paypal_payment = settings.url_auto_paypal_payment or {}
                        url_max_prices_map = settings.url_max_prices or {}
                        max_price = url_max_prices_map.get(url)
                        auto_buy_enabled = bool(url_auto_add.get(url))

                        if not status.available:
                            self._cart_added_for_cycle[url] = False

                        should_try_cart = (
                            status.available is True
                            and auto_buy_enabled
                            and isinstance(checker, BrowserProductChecker)
                            and not self._cart_added_for_cycle.get(url, False)
                        )

                        if should_try_cart:
                            price_ok = (
                                status.price is None
                                or max_price is None
                                or status.price <= max_price
                            )
                            if price_ok:
                                auto_paypal_requested = bool(url_auto_paypal_payment.get(url))
                                if auto_paypal_requested and settings.browser_headless:
                                    cart_result = CartActionResult(
                                        success=False,
                                        error="auto_paypal_requires_browser_headless_false",
                                        price_detected=status.price,
                                        stage="preflight",
                                    )
                                elif auto_paypal_requested and not settings.browser_cdp_url and not settings.browser_user_data_dir:
                                    cart_result = CartActionResult(
                                        success=False,
                                        error="auto_paypal_requires_browser_session_profile",
                                        price_detected=status.price,
                                        stage="preflight",
                                    )
                                else:
                                    try:
                                        cart_result = checker.add_to_cart(
                                            url,
                                            auto_checkout=bool(url_auto_checkout.get(url)),
                                            auto_paypal_payment=bool(url_auto_paypal_payment.get(url)),
                                            max_price=max_price,
                                        )
                                    except Exception:
                                        logging.exception("add_to_cart failed for %s", url)
                                        cart_result = CartActionResult(
                                            success=False,
                                            error="add_to_cart exception (see log)",
                                            stage="add_to_cart_exception",
                                        )
                                if cart_result.success:
                                    self._cart_added_for_cycle[url] = True
                            else:
                                cart_result = CartActionResult(
                                    success=False,
                                    error="price_above_max",
                                    price_detected=status.price,
                                )

                        if should_alert and not auto_buy_enabled:
                            non_retry_errors = {
                                "price_above_max",
                                "amazon_cart_empty_after_continue",
                                "walmart_marketplace_seller",
                                "walmart_seller_unconfirmed",
                                "walmart_price_not_detected_for_threshold",
                                "walmart_captcha_manual_required",
                            }
                            telegram.send_message(
                                self._format_alert_message(
                                    status.title,
                                    status.url,
                                    price_str,
                                    cart_result,
                                    max_price if auto_buy_enabled else None,
                                    auto_checkout=bool(url_auto_checkout.get(url)),
                                    auto_paypal_payment=bool(url_auto_paypal_payment.get(url)),
                                )
                            )
                            alert_time = datetime.now().isoformat(timespec="seconds")
                            if cart_result is not None and not cart_result.success and cart_result.error not in non_retry_errors:
                                # Keep checking quickly while auto-cart retries are still failing.
                                next_check = time.time() + 2
                            else:
                                next_check = time.time() + settings.alert_recheck_cooldown_seconds

                        elif should_try_cart and cart_result is not None and not cart_result.success and cart_result.error not in {
                            "price_above_max",
                            "amazon_cart_empty_after_continue",
                            "walmart_marketplace_seller",
                            "walmart_seller_unconfirmed",
                            "walmart_price_not_detected_for_threshold",
                            "walmart_captcha_manual_required",
                        }:
                            # No alert transition this cycle, but we still need persistent cart retries.
                            next_check = time.time() + 2

                        if auto_buy_enabled and cart_result is not None and cart_result.success and cart_result.payment_completed:
                            telegram.send_message(
                                self._format_purchase_completed_message(
                                    status.title,
                                    status.url,
                                    price_str,
                                    cart_result.order_id,
                                )
                            )

                        with self._lock:
                            entry = self._runtime.setdefault(url, UrlRuntimeStatus(url=url))
                            if configured_title:
                                entry.title = configured_title
                            else:
                                entry.title = status.title or entry.title
                            entry.available = bool(status.available)
                            entry.reason = status.reason
                            entry.last_checked_at = checked_at
                            entry.last_alert_at = alert_time or entry.last_alert_at
                            entry.next_check_at_epoch = next_check
                            entry.error = ""
                            self._next_check_at[url] = next_check
                            if price_str:
                                entry.last_price = price_str
                            if cart_result is not None:
                                if cart_result.success:
                                    entry.last_cart_added_at = datetime.now().isoformat(timespec="seconds")
                                    entry.cart_add_error = ""
                                    entry.last_checkout_stage = cart_result.stage
                                    entry.payment_completed = bool(cart_result.payment_completed)
                                else:
                                    error_msg = cart_result.error
                                    if cart_result.attempts > 1:
                                        error_msg = f"{error_msg} (attempts: {cart_result.attempts})"
                                    entry.cart_add_error = error_msg
                                    entry.last_checkout_stage = cart_result.stage
                                    entry.payment_completed = False

                        self._state[url] = {
                            "available": bool(status.available),
                            "title": entry.title,
                            "last_checked_at": checked_at,
                        }
                        save_state(state_file, self._state)

                    except Exception as exc:
                        logging.exception("Check failed for URL %s", url)
                        with self._lock:
                            entry = self._runtime.setdefault(url, UrlRuntimeStatus(url=url))
                            entry.last_checked_at = checked_at
                            entry.error = str(exc)
                            entry.reason = "check_error"
                            entry.next_check_at_epoch = time.time() + max(5, settings.check_interval_seconds)
                            self._next_check_at[url] = entry.next_check_at_epoch

                # Sleep until the next URL is due (supports short retry intervals), but never longer than the configured interval.
                with self._lock:
                    if active_urls:
                        next_due_epoch = min(self._next_check_at.get(u, time.time() + settings.check_interval_seconds) for u in active_urls)
                        wait_seconds = max(0.5, min(settings.check_interval_seconds, max(0.0, next_due_epoch - time.time())))
                    else:
                        wait_seconds = max(1.0, settings.check_interval_seconds)

                self._stop_event.wait(timeout=wait_seconds)

        except Exception as exc:
            logging.exception("Monitor service crashed")
            with self._lock:
                self._last_error = str(exc)
        finally:
            try:
                save_state(state_file, self._state)
            except Exception:
                pass

            if browser_checker is not None:
                try:
                    browser_checker.close()
                except Exception:
                    pass

            with self._lock:
                self._thread = None

    @staticmethod
    def _store_for_url(url: str) -> str:
        raw = (url or "").lower()
        if "amazon." in raw:
            return "amazon"
        if "target." in raw:
            return "target"
        if "walmart." in raw:
            return "walmart"
        return "other"

    def _is_store_enabled(self, url: str, monitored_stores: list[str] | None) -> bool:
        if monitored_stores is None:
            return True
        normalized = {str(s or "").strip().lower() for s in monitored_stores}
        if not normalized:
            return False
        store = self._store_for_url(url)
        return store in normalized

    @staticmethod
    def _format_alert_message(
        title: str,
        url: str,
        price_str: str = "",
        cart_result=None,
        max_price: float | None = None,
        auto_checkout: bool = False,
        auto_paypal_payment: bool = False,
    ) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            "Product is now available for Add to Cart.",
            f"Time: {now}",
            f"Title: {title}",
        ]
        if price_str:
            lines.append(f"Price: {price_str}")
        lines.append(f"URL: {url}")

        if cart_result is not None:
            lines.append("")
            if cart_result.success:
                lines.append("Added to cart!")
                if auto_checkout:
                    lines.append(f"Checkout stage: {cart_result.stage}")
                if auto_paypal_payment:
                    lines.append(f"PayPal payment completed: {'yes' if cart_result.payment_completed else 'no'}")
                if cart_result.order_id:
                    lines.append(f"Order ID: {cart_result.order_id}")
                lines.append(f"Checkout: {cart_result.checkout_url}")
            elif cart_result.error == "price_above_max":
                price_show = f"${cart_result.price_detected:.2f}" if cart_result.price_detected is not None else "N/A"
                max_show = f"${max_price:.2f}" if max_price is not None else "N/A"
                lines.append(f"Auto add-to-cart skipped: price {price_show} above max {max_show}")
            else:
                lines.append(f"Auto checkout failed at stage '{cart_result.stage}': {cart_result.error}")
                if cart_result.manual_action_url:
                    lines.append(f"Manual recovery: {cart_result.manual_action_url}")

        return "\n".join(lines)

    @staticmethod
    def _format_cart_retry_success_message(title: str, url: str, price_str: str = "") -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            "Auto add-to-cart retry succeeded.",
            f"Time: {now}",
            f"Title: {title}",
        ]
        if price_str:
            lines.append(f"Price: {price_str}")
        lines.append(f"URL: {url}")
        lines.append("Checkout: https://www.target.com/cart")
        return "\n".join(lines)

    @staticmethod
    def _format_purchase_completed_message(title: str, url: str, price_str: str = "", order_id: str = "") -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        store = MonitorService._store_label_from_url(url)
        lines = [
            "Articulo comprado exitosamente.",
            f"Hora: {now}",
            f"Tienda: {store}",
            f"Titulo: {title}",
        ]
        if price_str:
            lines.append(f"Precio: {price_str}")
        if order_id:
            lines.append(f"Numero de pedido: {order_id}")
        lines.append(f"URL: {url}")
        return "\n".join(lines)

    @staticmethod
    def _store_label_from_url(url: str) -> str:
        raw = (url or "").lower()
        if "amazon." in raw:
            return "Amazon"
        if "target." in raw:
            return "Target"
        if "walmart." in raw:
            return "Walmart"
        return "Other"

    @staticmethod
    def _parse_monitor_start_time_local(value: str) -> dt_time | None:
        raw = str(value or "").strip()
        if not raw:
            return None

        hour_text, minute_text = raw.split(":", 1)
        return dt_time(hour=int(hour_text), minute=int(minute_text))

    @classmethod
    def _wait_seconds_until_start(cls, scheduled_time_local: str) -> float:
        target_time = cls._parse_monitor_start_time_local(scheduled_time_local)
        if target_time is None:
            return 0.0

        now = datetime.now()
        target = datetime.combine(now.date(), target_time)
        return max(0.0, (target - now).total_seconds())

    @classmethod
    def _is_waiting_for_start_time(cls, scheduled_time_local: str) -> bool:
        return cls._wait_seconds_until_start(scheduled_time_local) > 0
