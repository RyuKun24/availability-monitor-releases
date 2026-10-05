from __future__ import annotations

from dataclasses import dataclass
import re

from .checker import ProductStatus, USER_AGENT


class BrowserCheckerUnavailableError(RuntimeError):
    """Raised when browser-based checking cannot be initialized."""


@dataclass
class CartActionResult:
    success: bool
    checkout_url: str = ""
    error: str = ""
    price_detected: float | None = None
    attempts: int = 1
    stage: str = "add_to_cart"
    payment_provider: str = ""
    payment_completed: bool = False
    manual_action_url: str = ""
    order_id: str = ""


@dataclass
class BrowserCheckerConfig:
    timeout_seconds: int = 20
    headless: bool = True
    user_data_dir: str = ""
    browser_channel: str = "chrome"
    browser_cdp_url: str = ""
    expected_ship_zip: str = ""
    enforce_ship_zip: bool = True


class BrowserProductChecker:
    def __init__(self, config: BrowserCheckerConfig) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # pragma: no cover
            raise BrowserCheckerUnavailableError(
                f"Playwright could not be imported ({type(exc).__name__}: {exc}). "
                "Install with: pip install playwright"
            ) from exc

        self._timeout_ms = max(5, config.timeout_seconds) * 1000
        raw_zip = (config.expected_ship_zip or "").strip()
        self._expected_ship_zips = [z.strip() for z in raw_zip.split(",") if z.strip()]
        self._enforce_ship_zip = bool(config.enforce_ship_zip)
        self._browser_cdp_url = (config.browser_cdp_url or "").strip()
        self._browser_channel = (config.browser_channel or "chrome").strip() or "chrome"
        self._config = config
        self._playwright = sync_playwright().start()
        self._browser = None
        self._context = None
        self._start_context()

    def _start_context(self) -> None:
        if self._context is not None:
            try:
                self._context.close()
            except Exception:
                pass
            self._context = None

        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None

        if self._browser_cdp_url:
            self._browser = self._playwright.chromium.connect_over_cdp(self._browser_cdp_url)
            if self._browser.contexts:
                self._context = self._browser.contexts[0]
            else:
                self._context = self._browser.new_context(locale="en-US", user_agent=USER_AGENT)
            return

        if self._browser_cdp_url:
            self._browser = self._playwright.chromium.connect_over_cdp(self._browser_cdp_url)
            if self._browser.contexts:
                self._context = self._browser.contexts[0]
            else:
                self._context = self._browser.new_context(locale="en-US", user_agent=USER_AGENT)
            return

        if self._config.user_data_dir:
            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=self._config.user_data_dir,
                headless=self._config.headless,
                locale="en-US",
                user_agent=USER_AGENT,
                channel=self._browser_channel,
            )
        else:
            self._browser = self._playwright.chromium.launch(headless=self._config.headless, channel=self._browser_channel)
            self._context = self._browser.new_context(locale="en-US", user_agent=USER_AGENT)

    def check(self, url: str) -> ProductStatus:
        if self._is_amazon_url(url):
            return self._check_amazon(url)
        if self._is_walmart_url(url):
            return self._check_walmart(url)

        for attempt in range(2):
            page = None
            try:
                if self._context is None:
                    self._start_context()

                page = self._context.new_page()
                response = page.goto(url, wait_until="domcontentloaded", timeout=self._timeout_ms)
                try:
                    page.wait_for_load_state("networkidle", timeout=min(15000, self._timeout_ms))
                except Exception:
                    pass

                if response is not None and response.status == 404:
                    title = (page.title() or "Unknown product").strip()
                    return ProductStatus(
                        url=url,
                        title=title,
                        available=False,
                        reason="http_404_prelaunch_or_missing",
                    )

                transient_overlay_reasons = {
                    "target_loading_overlay",
                    "target_blocking_error_overlay",
                }
                for overlay_attempt in range(3):
                    self._wait_for_non_blocking_ui(page)
                    blocking_reason = self._read_blocking_overlay_reason(page)
                    if not blocking_reason:
                        break
                    if blocking_reason in transient_overlay_reasons and overlay_attempt < 2:
                        try:
                            page.wait_for_timeout(1200)
                            page.reload(wait_until="domcontentloaded", timeout=self._timeout_ms)
                            page.wait_for_timeout(1200)
                        except Exception:
                            pass
                        continue

                    title = (page.title() or "Unknown product").strip()
                    return ProductStatus(url=url, title=title, available=False, reason=blocking_reason)

                title = (page.title() or "Unknown product").strip()
                main = page.locator("main").first
                scope = main if main.count() > 0 else page.locator("body").first
                scope_text = scope.inner_text().lower()

                if self._expected_ship_zips:
                    body_text = page.locator("body").inner_text().lower()
                    if self._enforce_ship_zip and not self._has_expected_ship_zip(body_text, self._expected_ship_zips):
                        detected_zip = self._extract_detected_ship_zip(body_text)
                        detail = f"expected_{'-'.join(self._expected_ship_zips)}"
                        if detected_zip:
                            detail = f"expected_{'-'.join(self._expected_ship_zips)}_got_{detected_zip}"
                        return ProductStatus(
                            url=url,
                            title=title,
                            available=False,
                            reason=f"ship_zip_mismatch_{detail}",
                        )

                for marker in (
                    "out of stock",
                    "sold out",
                    "temporarily out of stock",
                    "unavailable",
                    "not available",
                    "unavailable in your area",
                    "item unavailable in your area",
                ):
                    if marker in scope_text:
                        return ProductStatus(url=url, title=title, available=False, reason=marker.replace(" ", "_"))

                body_text = page.locator("body").inner_text().lower()
                for marker in (
                    "unavailable in your area",
                    "item unavailable in your area",
                ):
                    if marker in body_text:
                        return ProductStatus(url=url, title=title, available=False, reason=marker.replace(" ", "_"))

                primary_btn, mode = self._find_primary_purchase_button(page)
                if primary_btn is not None:
                    disabled_attr = primary_btn.get_attribute("disabled")
                    aria_disabled = (primary_btn.get_attribute("aria-disabled") or "").lower()
                    class_attr = (primary_btn.get_attribute("class") or "").lower()
                    is_disabled = (
                        disabled_attr is not None
                        or aria_disabled == "true"
                        or "disabled" in class_attr
                    )
                    if not is_disabled and primary_btn.is_enabled():
                        price = self._extract_price(page)
                        return ProductStatus(url=url, title=title, available=True, reason=f"{mode}_enabled_browser", price=price)
                    return ProductStatus(url=url, title=title, available=False, reason=f"{mode}_disabled_browser")

                return ProductStatus(url=url, title=title, available=False, reason="add_to_cart_not_found_browser")

            except Exception as exc:
                message = str(exc).lower()
                recoverable = (
                    "target page, context or browser has been closed" in message
                    or "failed to open a new tab" in message
                    or "err_aborted" in message
                )
                if attempt == 0 and recoverable:
                    self._start_context()
                    continue
                raise
            finally:
                if page is not None:
                    try:
                        page.close()
                    except Exception:
                        pass

        raise BrowserCheckerUnavailableError("Unable to evaluate page after browser restart attempts")

    def _check_amazon(self, url: str) -> ProductStatus:
        for attempt in range(2):
            page = None
            try:
                if self._context is None:
                    self._start_context()

                page = self._context.new_page()
                response = page.goto(url, wait_until="domcontentloaded", timeout=self._timeout_ms)
                try:
                    page.wait_for_load_state("networkidle", timeout=min(15000, self._timeout_ms))
                except Exception:
                    pass

                title = (page.title() or "Unknown product").strip()

                if response is not None and response.status == 404:
                    return ProductStatus(
                        url=url,
                        title=title,
                        available=False,
                        reason="http_404_prelaunch_or_missing",
                    )

                body_text = (page.locator("body").inner_text(timeout=4000) or "").lower()
                unavailable_markers = (
                    "por el momento no podemos enviar",
                    "actualiza tus productos",
                    "agotado",
                    "temporalmente sin stock",
                    "currently unavailable",
                    "out of stock",
                    "no disponible",
                    "no podemos entregar",
                )
                for marker in unavailable_markers:
                    if marker in body_text:
                        return ProductStatus(url=url, title=title, available=False, reason=f"amazon_{marker.replace(' ', '_')}")

                primary_btn, mode = self._find_primary_purchase_button(page)
                if primary_btn is not None:
                    disabled_attr = primary_btn.get_attribute("disabled")
                    aria_disabled = (primary_btn.get_attribute("aria-disabled") or "").lower()
                    class_attr = (primary_btn.get_attribute("class") or "").lower()
                    is_disabled = (
                        disabled_attr is not None
                        or aria_disabled == "true"
                        or "disabled" in class_attr
                    )
                    if not is_disabled and primary_btn.is_enabled():
                        price = self._extract_price(page)
                        return ProductStatus(url=url, title=title, available=True, reason=f"{mode}_enabled_browser", price=price)
                    return ProductStatus(url=url, title=title, available=False, reason=f"{mode}_disabled_browser")

                return ProductStatus(url=url, title=title, available=False, reason="amazon_purchase_button_not_found")

            except Exception as exc:
                message = str(exc).lower()
                recoverable = (
                    "target page, context or browser has been closed" in message
                    or "failed to open a new tab" in message
                    or "err_aborted" in message
                )
                if attempt == 0 and recoverable:
                    self._start_context()
                    continue
                raise
            finally:
                if page is not None:
                    try:
                        page.close()
                    except Exception:
                        pass

        raise BrowserCheckerUnavailableError("Unable to evaluate Amazon page after browser restart attempts")

    def _check_walmart(self, url: str) -> ProductStatus:
        for attempt in range(2):
            page = None
            try:
                if self._context is None:
                    self._start_context()

                page = self._context.new_page()
                response = page.goto(url, wait_until="domcontentloaded", timeout=self._timeout_ms)
                try:
                    page.wait_for_load_state("networkidle", timeout=min(15000, self._timeout_ms))
                except Exception:
                    pass

                title = (page.title() or "Unknown product").strip()

                if response is not None and response.status == 404:
                    return ProductStatus(
                        url=url,
                        title=title,
                        available=False,
                        reason="http_404_prelaunch_or_missing",
                    )

                body_text = self._get_page_text(page)
                if self._is_walmart_hold_captcha_present(body_text):
                    return ProductStatus(
                        url=url,
                        title=title,
                        available=False,
                        reason="walmart_captcha_challenge_active",
                    )

                seller_status = self._classify_walmart_seller(body_text)
                if seller_status == "marketplace":
                    return ProductStatus(url=url, title=title, available=False, reason="walmart_marketplace_seller")
                if seller_status != "walmart":
                    return ProductStatus(url=url, title=title, available=False, reason="walmart_seller_unconfirmed")

                price = self._extract_price_walmart(page)
                queue_button, queue_reason = self._find_walmart_queue_button(page)
                if queue_button is not None:
                    return ProductStatus(
                        url=url,
                        title=title,
                        available=True,
                        reason=queue_reason,
                        price=price,
                    )

                if self._is_walmart_queue_active_text(body_text):
                    return ProductStatus(
                        url=url,
                        title=title,
                        available=True,
                        reason="walmart_queue_active",
                        price=price,
                    )

                primary_btn, mode = self._find_primary_purchase_button(page)
                if primary_btn is not None:
                    disabled_attr = primary_btn.get_attribute("disabled")
                    aria_disabled = (primary_btn.get_attribute("aria-disabled") or "").lower()
                    class_attr = (primary_btn.get_attribute("class") or "").lower()
                    is_disabled = (
                        disabled_attr is not None
                        or aria_disabled == "true"
                        or "disabled" in class_attr
                    )
                    if not is_disabled and primary_btn.is_enabled():
                        return ProductStatus(url=url, title=title, available=True, reason=f"walmart_{mode}_enabled_browser", price=price)

                unavailable_markers = (
                    "out of stock",
                    "currently out of stock",
                    "this item is out of stock",
                    "sold out",
                    "item unavailable",
                    "not available online",
                    "unavailable for pickup",
                    "unavailable for shipping",
                )
                for marker in unavailable_markers:
                    if marker in body_text:
                        return ProductStatus(url=url, title=title, available=False, reason=f"walmart_{marker.replace(' ', '_')}")

                if primary_btn is not None:
                    return ProductStatus(url=url, title=title, available=False, reason=f"walmart_{mode}_disabled_browser", price=price)

                return ProductStatus(url=url, title=title, available=False, reason="walmart_purchase_button_not_found")

            except Exception as exc:
                message = str(exc).lower()
                recoverable = (
                    "target page, context or browser has been closed" in message
                    or "failed to open a new tab" in message
                    or "err_aborted" in message
                )
                if attempt == 0 and recoverable:
                    self._start_context()
                    continue
                raise
            finally:
                if page is not None:
                    try:
                        page.close()
                    except Exception:
                        pass

        raise BrowserCheckerUnavailableError("Unable to evaluate Walmart page after browser restart attempts")

    def add_to_cart(
        self,
        url: str,
        max_attempts: int = 8,
        retry_delay_ms: int = 1500,
        auto_checkout: bool = False,
        auto_paypal_payment: bool = False,
        max_price: float | None = None,
    ) -> CartActionResult:
        """Attempt to add product to cart with retries for transient high-demand failures."""
        if self._is_amazon_url(url):
            return self._add_to_cart_amazon(
                url,
                max_attempts=max_attempts,
                retry_delay_ms=retry_delay_ms,
                max_price=max_price,
            )
        if self._is_walmart_url(url):
            return self._add_to_cart_walmart(
                url,
                max_attempts=max_attempts,
                retry_delay_ms=retry_delay_ms,
                max_price=max_price,
            )

        page = None
        attempts = 0
        last_error = ""
        last_price = None
        try:
            if self._context is None:
                self._start_context()

            page = self._context.new_page()
            response = page.goto(url, wait_until="domcontentloaded", timeout=self._timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=min(15000, self._timeout_ms))
            except Exception:
                pass

            if response is not None and response.status == 404:
                return CartActionResult(success=False, error="page_not_found_404", stage="open_product")

            while True:
                attempts += 1
                self._wait_for_non_blocking_ui(page)
                blocking_reason = self._read_blocking_overlay_reason(page)
                if blocking_reason:
                    last_error = f"blocking_overlay:{blocking_reason}"
                    if attempts >= max_attempts:
                        return CartActionResult(
                            success=False,
                            error=last_error,
                            price_detected=last_price,
                            attempts=attempts,
                            stage="pre_click_checks",
                            manual_action_url=url,
                        )
                    page.wait_for_timeout(retry_delay_ms)
                    try:
                        page.reload(wait_until="domcontentloaded", timeout=self._timeout_ms)
                    except Exception:
                        pass
                    continue

                last_price = self._extract_price(page)

                primary_btn, _ = self._find_primary_purchase_button(page)
                if primary_btn is None:
                    last_error = "no_purchase_button_found"
                    if attempts >= max_attempts:
                        return CartActionResult(
                            success=False,
                            error=last_error,
                            price_detected=last_price,
                            attempts=attempts,
                            stage="find_purchase_button",
                            manual_action_url=url,
                        )
                    page.wait_for_timeout(retry_delay_ms)
                    try:
                        page.reload(wait_until="domcontentloaded", timeout=self._timeout_ms)
                    except Exception:
                        pass
                    continue

                disabled_attr = primary_btn.get_attribute("disabled")
                aria_disabled = (primary_btn.get_attribute("aria-disabled") or "").lower()
                class_attr = (primary_btn.get_attribute("class") or "").lower()
                is_disabled = (
                    disabled_attr is not None
                    or aria_disabled == "true"
                    or "disabled" in class_attr
                )
                if is_disabled or not primary_btn.is_enabled():
                    last_error = "button_disabled_at_click_time"
                    if attempts >= max_attempts:
                        return CartActionResult(
                            success=False,
                            error=last_error,
                            price_detected=last_price,
                            attempts=attempts,
                            stage="button_enabled_check",
                            manual_action_url=url,
                        )
                    page.wait_for_timeout(retry_delay_ms)
                    try:
                        page.reload(wait_until="domcontentloaded", timeout=self._timeout_ms)
                    except Exception:
                        pass
                    continue

                try:
                    primary_btn.click(timeout=min(8000, self._timeout_ms))
                except Exception as exc:
                    last_error = f"click_failed:{str(exc)[:120]}"
                    if attempts >= max_attempts:
                        return CartActionResult(
                            success=False,
                            error=last_error,
                            price_detected=last_price,
                            attempts=attempts,
                            stage="click_add_to_cart",
                            manual_action_url=url,
                        )
                    page.wait_for_timeout(retry_delay_ms)
                    continue

                if self._cart_add_success_detected(page):
                    result = CartActionResult(
                        success=True,
                        checkout_url="https://www.target.com/cart",
                        price_detected=last_price,
                        attempts=attempts,
                        stage="added_to_cart",
                        manual_action_url="https://www.target.com/cart",
                    )
                    if auto_checkout:
                        return self._run_checkout_flow(
                            page,
                            price_detected=last_price,
                            attempts=attempts,
                            auto_paypal_payment=auto_paypal_payment,
                        )
                    return result

                transient_reason = self._extract_transient_cart_failure_reason(page)
                last_error = transient_reason or "cart_add_not_confirmed"
                if attempts >= max_attempts:
                    return CartActionResult(
                        success=False,
                        error=last_error,
                        price_detected=last_price,
                        attempts=attempts,
                        stage="confirm_add_to_cart",
                        manual_action_url="https://www.target.com/cart",
                    )

                page.wait_for_timeout(retry_delay_ms)
                try:
                    page.reload(wait_until="domcontentloaded", timeout=self._timeout_ms)
                except Exception:
                    pass
        except Exception as exc:
            return CartActionResult(
                success=False,
                error=str(exc)[:300],
                price_detected=last_price,
                attempts=max(1, attempts),
                stage="add_to_cart_exception",
                manual_action_url="https://www.target.com/cart",
            )
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass

    def _add_to_cart_amazon(
        self,
        url: str,
        max_attempts: int,
        retry_delay_ms: int,
        max_price: float | None,
    ) -> CartActionResult:
        page = None
        attempts = 0
        last_price = None
        try:
            if self._context is None:
                self._start_context()

            page = self._context.new_page()
            while attempts < max_attempts:
                attempts += 1
                page.goto(url, wait_until="domcontentloaded", timeout=self._timeout_ms)
                try:
                    page.wait_for_load_state("networkidle", timeout=min(15000, self._timeout_ms))
                except Exception:
                    pass

                last_price = self._extract_price_amazon(page)
                if max_price is not None:
                    if last_price is None:
                        return CartActionResult(
                            success=False,
                            error="price_not_detected_for_threshold",
                            price_detected=last_price,
                            attempts=attempts,
                            stage="amazon_open_product",
                            manual_action_url=url,
                        )
                    if last_price > max_price:
                        return CartActionResult(
                            success=False,
                            error="price_above_max",
                            price_detected=last_price,
                            attempts=attempts,
                            stage="amazon_open_product",
                            manual_action_url=url,
                        )

                reserve_btn = self._find_amazon_reserve_button(page)
                if reserve_btn is None:
                    if attempts >= max_attempts:
                        return CartActionResult(
                            success=False,
                            error="amazon_reserve_button_not_found",
                            price_detected=last_price,
                            attempts=attempts,
                            stage="amazon_reserve_click",
                            manual_action_url=url,
                        )
                    page.wait_for_timeout(retry_delay_ms)
                    continue

                disabled_attr = reserve_btn.get_attribute("disabled")
                aria_disabled = (reserve_btn.get_attribute("aria-disabled") or "").lower()
                class_attr = (reserve_btn.get_attribute("class") or "").lower()
                is_disabled = (
                    disabled_attr is not None
                    or aria_disabled == "true"
                    or "disabled" in class_attr
                )
                if is_disabled or not reserve_btn.is_enabled():
                    if attempts >= max_attempts:
                        return CartActionResult(
                            success=False,
                            error="amazon_reserve_button_disabled",
                            price_detected=last_price,
                            attempts=attempts,
                            stage="amazon_reserve_click",
                            manual_action_url=url,
                        )
                    page.wait_for_timeout(retry_delay_ms)
                    continue

                try:
                    reserve_btn.click(timeout=min(9000, self._timeout_ms))
                except Exception as exc:
                    if attempts >= max_attempts:
                        return CartActionResult(
                            success=False,
                            error=f"amazon_reserve_click_failed:{str(exc)[:120]}",
                            price_detected=last_price,
                            attempts=attempts,
                            stage="amazon_reserve_click",
                            manual_action_url=url,
                        )
                    page.wait_for_timeout(retry_delay_ms)
                    continue

                page.wait_for_timeout(2000)
                checkout_page = self._resolve_active_checkout_page(page)

                checkout_page, continue_ok, continue_error = self._recover_amazon_checkout_error(
                    checkout_page,
                    url,
                    max_price,
                )
                if not continue_ok:
                    if continue_error == "amazon_cart_empty_after_continue":
                        return CartActionResult(
                            success=False,
                            error=continue_error,
                            price_detected=last_price,
                            attempts=attempts,
                            stage="amazon_retry_continue",
                            manual_action_url=url,
                        )
                    if attempts >= max_attempts:
                        return CartActionResult(
                            success=False,
                            error="amazon_checkout_error_continue_failed",
                            price_detected=last_price,
                            attempts=attempts,
                            stage="amazon_retry_continue",
                            manual_action_url=(checkout_page.url or "") or url,
                        )
                    page.wait_for_timeout(retry_delay_ms)
                    continue

                checkout_price = self._extract_price_amazon(checkout_page)
                if max_price is not None:
                    if checkout_price is None:
                        return CartActionResult(
                            success=False,
                            error="price_not_detected_pre_submit",
                            price_detected=checkout_price,
                            attempts=attempts,
                            stage="amazon_checkout_review",
                            manual_action_url=(checkout_page.url or "") or url,
                        )
                    if checkout_price > max_price:
                        return CartActionResult(
                            success=False,
                            error="price_above_max_pre_submit",
                            price_detected=checkout_price,
                            attempts=attempts,
                            stage="amazon_checkout_review",
                            manual_action_url=(checkout_page.url or "") or url,
                        )

                place_order_btn = self._find_amazon_place_order_button(checkout_page)
                if place_order_btn is None:
                    if attempts >= max_attempts:
                        return CartActionResult(
                            success=False,
                            error="amazon_place_order_button_not_found",
                            price_detected=checkout_price,
                            attempts=attempts,
                            stage="amazon_place_order",
                            manual_action_url=(checkout_page.url or "") or url,
                        )
                    page.wait_for_timeout(retry_delay_ms)
                    continue

                try:
                    place_order_btn.click(timeout=min(9000, self._timeout_ms))
                except Exception as exc:
                    if attempts >= max_attempts:
                        return CartActionResult(
                            success=False,
                            error=f"amazon_place_order_click_failed:{str(exc)[:120]}",
                            price_detected=checkout_price,
                            attempts=attempts,
                            stage="amazon_place_order",
                            manual_action_url=(checkout_page.url or "") or url,
                        )
                    page.wait_for_timeout(retry_delay_ms)
                    continue

                checkout_page.wait_for_timeout(3500)
                post_page = self._resolve_active_checkout_page(checkout_page)
                order_id = self._extract_amazon_order_id(post_page)
                if self._is_amazon_order_confirmation_page(post_page) and order_id:
                    return CartActionResult(
                        success=True,
                        checkout_url=(post_page.url or "") or "https://www.amazon.com.mx/gp/buy/spc/handlers/display.html",
                        price_detected=checkout_price,
                        attempts=attempts,
                        stage="amazon_order_confirmation",
                        payment_provider="amazon",
                        payment_completed=True,
                        manual_action_url=(post_page.url or "") or url,
                        order_id=order_id,
                    )

                if attempts >= max_attempts:
                    return CartActionResult(
                        success=False,
                        checkout_url=(post_page.url or "") or "https://www.amazon.com.mx/gp/buy/spc/handlers/display.html",
                        error="amazon_order_confirmation_not_detected",
                        price_detected=checkout_price,
                        attempts=attempts,
                        stage="amazon_order_confirmation",
                        payment_provider="amazon",
                        manual_action_url=(post_page.url or "") or url,
                    )

                page.wait_for_timeout(retry_delay_ms)

            return CartActionResult(
                success=False,
                error="amazon_max_attempts_reached",
                price_detected=last_price,
                attempts=attempts,
                stage="amazon_open_product",
                manual_action_url=url,
            )
        except Exception as exc:
            return CartActionResult(
                success=False,
                error=f"amazon_flow_exception:{str(exc)[:180]}",
                price_detected=last_price,
                attempts=max(1, attempts),
                stage="amazon_open_product",
                manual_action_url=url,
            )
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass

    def _add_to_cart_walmart(
        self,
        url: str,
        max_attempts: int,
        retry_delay_ms: int,
        max_price: float | None,
    ) -> CartActionResult:
        page = None
        attempts = 0
        last_price = None
        try:
            if self._context is None:
                self._start_context()

            page = self._context.new_page()
            response = page.goto(url, wait_until="domcontentloaded", timeout=self._timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=min(15000, self._timeout_ms))
            except Exception:
                pass

            if response is not None and response.status == 404:
                return CartActionResult(success=False, error="page_not_found_404", stage="walmart_open_product", manual_action_url=url)

            while attempts < max_attempts:
                attempts += 1
                self._wait_for_non_blocking_ui(page)
                blocking_reason = self._read_blocking_overlay_reason(page)
                if blocking_reason:
                    if attempts >= max_attempts:
                        return CartActionResult(
                            success=False,
                            error=f"blocking_overlay:{blocking_reason}",
                            price_detected=last_price,
                            attempts=attempts,
                            stage="walmart_pre_click_checks",
                            manual_action_url=url,
                        )
                    page.wait_for_timeout(retry_delay_ms)
                    continue

                if self._is_walmart_hold_captcha_present(self._get_page_text(page)):
                    captcha_cleared = self._wait_for_walmart_captcha_clear(page)
                    if not captcha_cleared:
                        return CartActionResult(
                            success=False,
                            error="walmart_captcha_manual_required",
                            price_detected=last_price,
                            attempts=attempts,
                            stage="walmart_captcha",
                            manual_action_url=url,
                        )

                ready, last_price, waiting_error = self._wait_for_walmart_turn(page, max_price)
                if not ready:
                    return CartActionResult(
                        success=False,
                        error=waiting_error,
                        price_detected=last_price,
                        attempts=attempts,
                        stage="walmart_queue_wait",
                        manual_action_url=url,
                    )

                primary_btn = self._find_walmart_purchase_button(page)
                if primary_btn is None:
                    if attempts >= max_attempts:
                        return CartActionResult(
                            success=False,
                            error="walmart_add_to_cart_button_not_found",
                            price_detected=last_price,
                            attempts=attempts,
                            stage="walmart_find_purchase_button",
                            manual_action_url=url,
                        )
                    page.wait_for_timeout(retry_delay_ms)
                    continue

                disabled_attr = primary_btn.get_attribute("disabled")
                aria_disabled = (primary_btn.get_attribute("aria-disabled") or "").lower()
                class_attr = (primary_btn.get_attribute("class") or "").lower()
                is_disabled = (
                    disabled_attr is not None
                    or aria_disabled == "true"
                    or "disabled" in class_attr
                )
                if is_disabled or not primary_btn.is_enabled():
                    if attempts >= max_attempts:
                        return CartActionResult(
                            success=False,
                            error="walmart_button_disabled_at_click_time",
                            price_detected=last_price,
                            attempts=attempts,
                            stage="walmart_button_enabled_check",
                            manual_action_url=url,
                        )
                    page.wait_for_timeout(retry_delay_ms)
                    continue

                try:
                    primary_btn.click(timeout=min(8000, self._timeout_ms))
                except Exception as exc:
                    if attempts >= max_attempts:
                        return CartActionResult(
                            success=False,
                            error=f"walmart_click_failed:{str(exc)[:120]}",
                            price_detected=last_price,
                            attempts=attempts,
                            stage="walmart_click_add_to_cart",
                            manual_action_url=url,
                        )
                    page.wait_for_timeout(retry_delay_ms)
                    continue

                if self._cart_add_success_detected(page):
                    return CartActionResult(
                        success=True,
                        checkout_url="https://www.walmart.com/cart",
                        price_detected=last_price,
                        attempts=attempts,
                        stage="walmart_added_to_cart",
                        manual_action_url="https://www.walmart.com/cart",
                    )

                transient_reason = self._extract_transient_cart_failure_reason(page)
                if attempts >= max_attempts:
                    return CartActionResult(
                        success=False,
                        error=transient_reason or "walmart_cart_add_not_confirmed",
                        price_detected=last_price,
                        attempts=attempts,
                        stage="walmart_confirm_add_to_cart",
                        manual_action_url="https://www.walmart.com/cart",
                    )

                page.wait_for_timeout(retry_delay_ms)
            return CartActionResult(
                success=False,
                error="walmart_max_attempts_reached",
                price_detected=last_price,
                attempts=max(1, attempts),
                stage="walmart_open_product",
                manual_action_url=url,
            )
        except Exception as exc:
            return CartActionResult(
                success=False,
                error=f"walmart_flow_exception:{str(exc)[:180]}",
                price_detected=last_price,
                attempts=max(1, attempts),
                stage="walmart_open_product",
                manual_action_url=url,
            )
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass

    def _run_checkout_flow(
        self,
        page,
        price_detected: float | None,
        attempts: int,
        auto_paypal_payment: bool,
    ) -> CartActionResult:
        cart_url = "https://www.target.com/cart"
        try:
            page.goto(cart_url, wait_until="domcontentloaded", timeout=self._timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=min(15000, self._timeout_ms))
            except Exception:
                pass

            blocker = self._detect_checkout_blocker(page)
            if blocker:
                return CartActionResult(
                    success=False,
                    checkout_url=cart_url,
                    error=blocker,
                    price_detected=price_detected,
                    attempts=attempts,
                    stage="cart_blocked",
                    manual_action_url=cart_url,
                )

            checkout_clicked = self._click_first_visible(page, [
                "button:has-text('Check out')",
                "button:has-text('Checkout')",
                "a:has-text('Check out')",
                "a:has-text('Checkout')",
                "button[data-test='checkout-button']",
                "button[data-test='checkoutButton']",
            ])
            if not checkout_clicked:
                return CartActionResult(
                    success=False,
                    checkout_url=cart_url,
                    error="checkout_button_not_found",
                    price_detected=price_detected,
                    attempts=attempts,
                    stage="open_checkout",
                    manual_action_url=cart_url,
                )

            page.wait_for_timeout(1800)
            checkout_page = self._resolve_active_checkout_page(page)
            checkout_url = (checkout_page.url or "").strip() or cart_url

            blocker = self._detect_checkout_blocker(checkout_page)
            if blocker:
                return CartActionResult(
                    success=False,
                    checkout_url=checkout_url,
                    error=blocker,
                    price_detected=price_detected,
                    attempts=attempts,
                    stage="checkout_blocked",
                    manual_action_url=checkout_url,
                )

            paypal_clicked = self._click_first_visible(checkout_page, [
                "button:has-text('PayPal')",
                "button[aria-label*='PayPal']",
                "button[data-test*='paypal']",
                "div[role='button']:has-text('PayPal')",
            ])
            if not paypal_clicked:
                return CartActionResult(
                    success=False,
                    checkout_url=checkout_url,
                    error="paypal_button_not_found",
                    price_detected=price_detected,
                    attempts=attempts,
                    stage="select_paypal",
                    payment_provider="paypal",
                    manual_action_url=checkout_url,
                )

            checkout_page.wait_for_timeout(2200)
            paypal_page = self._resolve_active_checkout_page(checkout_page)
            paypal_url = (paypal_page.url or "").strip() or checkout_url

            blocker = self._detect_checkout_blocker(paypal_page)
            if blocker:
                return CartActionResult(
                    success=False,
                    checkout_url=paypal_url,
                    error=blocker,
                    price_detected=price_detected,
                    attempts=attempts,
                    stage="paypal_auth",
                    payment_provider="paypal",
                    manual_action_url=paypal_url,
                )

            if not auto_paypal_payment:
                return CartActionResult(
                    success=True,
                    checkout_url=paypal_url,
                    price_detected=price_detected,
                    attempts=attempts,
                    stage="paypal_ready",
                    payment_provider="paypal",
                    manual_action_url=paypal_url,
                )

            final_clicked = self._click_first_visible(paypal_page, [
                "button:has-text('Place order')",
                "button:has-text('Pay now')",
                "button:has-text('Complete purchase')",
                "button[data-test*='placeOrder']",
                "button[id*='placeOrder']",
            ])
            if not final_clicked:
                return CartActionResult(
                    success=False,
                    checkout_url=paypal_url,
                    error="final_payment_button_not_found",
                    price_detected=price_detected,
                    attempts=attempts,
                    stage="payment_confirm",
                    payment_provider="paypal",
                    manual_action_url=paypal_url,
                )

            paypal_page.wait_for_timeout(3200)
            post_page = self._resolve_active_checkout_page(paypal_page)
            post_url = (post_page.url or "").strip() or paypal_url

            if self._is_order_confirmation_page(post_page):
                return CartActionResult(
                    success=True,
                    checkout_url=post_url,
                    price_detected=price_detected,
                    attempts=attempts,
                    stage="order_submitted",
                    payment_provider="paypal",
                    payment_completed=True,
                    manual_action_url=post_url,
                )

            blocker = self._detect_checkout_blocker(post_page)
            return CartActionResult(
                success=False,
                checkout_url=post_url,
                error=blocker or "order_confirmation_not_detected",
                price_detected=price_detected,
                attempts=attempts,
                stage="payment_post_submit",
                payment_provider="paypal",
                manual_action_url=post_url,
            )

        except Exception as exc:
            return CartActionResult(
                success=False,
                checkout_url=cart_url,
                error=f"checkout_exception:{str(exc)[:180]}",
                price_detected=price_detected,
                attempts=attempts,
                stage="checkout_exception",
                payment_provider="paypal" if auto_paypal_payment else "",
                manual_action_url=cart_url,
            )

    @staticmethod
    def _click_first_visible(page, selectors: list[str]) -> bool:
        for selector in selectors:
            loc = page.locator(selector)
            try:
                count = loc.count()
            except Exception:
                count = 0
            for idx in range(count):
                node = loc.nth(idx)
                try:
                    if not node.is_visible() or not node.is_enabled():
                        continue
                    node.click(timeout=5000)
                    return True
                except Exception:
                    continue
        return False

    def _resolve_active_checkout_page(self, current_page):
        try:
            pages = list(self._context.pages)
        except Exception:
            return current_page

        if not pages:
            return current_page

        priority_markers = (
            "paypal.com",
            "target.com/checkout",
            "target.com/cart",
            "target.com/co",
            "amazon.com.mx/gp/buy",
            "amazon.com.mx/checkout",
            "amazon.com.mx/gp/cart",
            "amazon.com.mx/gp/aw/c",
        )
        for page in reversed(pages):
            try:
                url = (page.url or "").lower()
            except Exception:
                continue
            if any(marker in url for marker in priority_markers):
                return page
        return pages[-1]

    @staticmethod
    def _detect_checkout_blocker(page) -> str:
        try:
            body_text = (page.locator("body").inner_text(timeout=3500) or "").lower()
        except Exception:
            return ""

        blocker_markers = {
            "captcha": "captcha_required",
            "verify you are": "captcha_required",
            "two-step verification": "paypal_mfa_required",
            "security challenge": "paypal_security_challenge",
            "enter the code": "paypal_mfa_required",
            "we need to confirm": "paypal_mfa_required",
            "session expired": "session_expired",
            "sign in": "paypal_signin_required",
        }
        for marker, code in blocker_markers.items():
            if marker in body_text:
                return code
        return ""

    @staticmethod
    def _is_order_confirmation_page(page) -> bool:
        try:
            text = (page.locator("body").inner_text(timeout=5000) or "").lower()
        except Exception:
            return False

        confirmation_markers = (
            "thank you for your order",
            "order confirmed",
            "order number",
            "we've received your order",
        )
        if any(marker in text for marker in confirmation_markers):
            return True

        try:
            url = (page.url or "").lower()
        except Exception:
            return False
        return "order-confirmation" in url or "thank-you" in url

    @staticmethod
    def _cart_add_success_detected(page) -> bool:
        """Detect explicit add-to-cart success indicators before reporting success."""
        try:
            page.wait_for_function(
                """
                () => {
                    const badge = document.querySelector("[data-test='cart-badge']");
                    if (badge && badge.textContent && badge.textContent.trim() !== "") return true;

                    const successBanner = Array.from(document.querySelectorAll("[role='alert'], [data-test*='toast'], [aria-live]"))
                        .some((el) => {
                            const t = (el.innerText || "").toLowerCase();
                            return t.includes("added to cart") || t.includes("added") || t.includes("in your cart");
                        });
                    if (successBanner) return true;

                    const miniCart = document.querySelector("[data-test='cartItemCount'], [data-test='cart-button']");
                    if (miniCart && /\\d+/.test((miniCart.textContent || "").trim())) return true;

                    return false;
                }
                """,
                timeout=6000,
            )
            return True
        except Exception:
            return False

    @staticmethod
    def _extract_transient_cart_failure_reason(page) -> str:
        """Extract common transient high-demand failure reasons shown by the site."""
        try:
            text = (page.locator("body").inner_text(timeout=2000) or "").lower()
        except Exception:
            return ""

        known_markers = {
            "high demand": "high_demand",
            "please try again": "please_try_again",
            "something went wrong": "something_went_wrong",
            "unable to add": "unable_to_add",
            "cannot add": "cannot_add",
            "can\'t add": "cant_add",
            "technical issue": "technical_issue",
            "cart is empty": "cart_is_empty",
            "not added": "not_added",
        }
        for marker, code in known_markers.items():
            if marker in text:
                return code
        return ""

    @staticmethod
    def _extract_price(page) -> float | None:
        """Extract the product price from the currently loaded page. Returns None if not found."""
        if BrowserProductChecker._is_amazon_page(page):
            return BrowserProductChecker._extract_price_amazon(page)
        if BrowserProductChecker._is_walmart_page(page):
            return BrowserProductChecker._extract_price_walmart(page)

        try:
            loc = page.locator("[data-test='product-price']").first
            if loc.count() > 0:
                text = (loc.inner_text(timeout=3000) or "").strip()
                price = BrowserProductChecker._parse_price_text(text)
                if price is not None:
                    return price
        except Exception:
            pass
        # Fallback: find first dollar amount in main content
        try:
            main_text = page.locator("main").first.inner_text(timeout=3000)
            return BrowserProductChecker._parse_price_text(main_text)
        except Exception:
            pass
        return None

    @staticmethod
    def _extract_price_amazon(page) -> float | None:
        selectors = [
            "#corePriceDisplay_desktop_feature_div .a-price",
            "#corePrice_feature_div .a-price",
            "#corePrice_desktop .a-price",
            "#apex_desktop .a-price",
            "span.a-price.aok-align-center .a-offscreen",
            "#price_inside_buybox",
            "#priceblock_ourprice",
            "#priceblock_dealprice",
            "#priceblock_saleprice",
            "span.a-offscreen",
        ]

        for selector in selectors:
            loc = page.locator(selector)
            try:
                count = loc.count()
            except Exception:
                count = 0
            for idx in range(min(count, 5)):
                node = loc.nth(idx)
                try:
                    if not node.is_visible():
                        continue
                    text = (node.inner_text(timeout=2000) or "").strip()
                    value = BrowserProductChecker._parse_price_text(text)
                    if value is not None:
                        return value
                except Exception:
                    continue

        try:
            whole = page.locator("span.a-price-whole").first
            frac = page.locator("span.a-price-fraction").first
            if whole.count() > 0 and whole.is_visible():
                whole_text = (whole.inner_text(timeout=2000) or "").strip().replace(".", "").replace(",", "")
                fraction_text = "00"
                if frac.count() > 0:
                    fraction_text = (frac.inner_text(timeout=2000) or "").strip()
                price_text = f"${whole_text}.{fraction_text}"
                value = BrowserProductChecker._parse_price_text(price_text)
                if value is not None:
                    return value
        except Exception:
            pass

        try:
            body_text = page.locator("body").inner_text(timeout=2500)
            return BrowserProductChecker._parse_price_text(body_text)
        except Exception:
            return None

    @staticmethod
    def _extract_price_walmart(page) -> float | None:
        selectors = [
            "[itemprop='price']",
            "[data-testid='price-wrap']",
            "[data-testid='product-price']",
            "span[data-automation-id='product-price']",
            "div[data-automation-id='product-price']",
        ]
        for selector in selectors:
            loc = page.locator(selector)
            try:
                count = loc.count()
            except Exception:
                count = 0
            for idx in range(min(count, 5)):
                node = loc.nth(idx)
                try:
                    if not node.is_visible():
                        continue
                    text = (node.inner_text(timeout=2000) or node.get_attribute("content") or "").strip()
                    value = BrowserProductChecker._parse_price_text(text)
                    if value is not None:
                        return value
                except Exception:
                    continue

        try:
            body_text = page.locator("body").inner_text(timeout=2500)
            return BrowserProductChecker._parse_price_text(body_text)
        except Exception:
            return None

    @staticmethod
    def _parse_price_text(text: str) -> float | None:
        """Parse the first dollar amount from *text*, e.g. '$29.99' → 29.99."""
        matches = re.findall(r"\$\s*([\d.,]+)", text or "")
        if not matches:
            return None
        try:
            raw = matches[0].strip()
            if "," in raw and "." in raw:
                raw = raw.replace(",", "")
            elif raw.count(",") == 1 and raw.count(".") == 0:
                left, right = raw.split(",", 1)
                if len(right) in (1, 2):
                    raw = f"{left}.{right}"
                else:
                    raw = raw.replace(",", "")
            else:
                raw = raw.replace(",", "")
            return float(raw)
        except (ValueError, IndexError):
            return None

    @staticmethod
    def _find_primary_purchase_button(page):
        if BrowserProductChecker._is_amazon_page(page):
            button = BrowserProductChecker._find_amazon_reserve_button(page)
            if button is not None:
                return button, "amazon_reserve"
            return None, "amazon_reserve"
        if BrowserProductChecker._is_walmart_page(page):
            button = BrowserProductChecker._find_walmart_purchase_button(page)
            if button is not None:
                return button, "add_to_cart"
            return None, "add_to_cart"

        selectors = [
            ("add_to_cart", "button[data-test='shippingButton']"),
            ("add_to_cart", "button[data-test='pickupButton']"),
            ("add_to_cart", "button[data-test='deliveryButton']"),
            ("add_to_cart", "button:has-text('Add to cart')"),
            ("buy_now", "button[data-test='buyNowButton']"),
            ("buy_now", "button:has-text('Buy now')"),
            ("buy_now", "button:has-text('buy now')"),
            ("buy_now", "button:has-text('Sign in to buy now')"),
        ]

        candidates = []
        for mode, selector in selectors:
            loc = page.locator(selector)
            for idx in range(loc.count()):
                btn = loc.nth(idx)
                try:
                    if not btn.is_visible():
                        continue
                    box = btn.bounding_box()
                    if not box:
                        continue
                    candidates.append((box.get("y", 999999), mode, btn))
                except Exception:
                    continue

        if not candidates:
            return None, "purchase"

        candidates.sort(key=lambda item: item[0])
        _, mode, button = candidates[0]
        return button, mode

    @staticmethod
    def _find_amazon_reserve_button(page):
        selectors = [
            "#buy-now-button",
            "#add-to-cart-button",
            "input[name='submit.buy-now']",
            "input[name='submit.add-to-cart']",
            "button:has-text('Reservar ahora')",
            "span:has-text('Reservar ahora')",
            "button:has-text('Comprar ahora')",
            "button:has-text('Buy Now')",
            "button:has-text('Add to Cart')",
        ]
        for selector in selectors:
            loc = page.locator(selector)
            try:
                count = loc.count()
            except Exception:
                count = 0
            for idx in range(count):
                node = loc.nth(idx)
                try:
                    if not node.is_visible():
                        continue
                    return node
                except Exception:
                    continue
        return None

    @staticmethod
    def _find_amazon_place_order_button(page):
        selectors = [
            "input[name='placeYourOrder1']",
            "input[name='submitOrderButtonId']",
            "button:has-text('Realiza tu pedido y paga')",
            "span:has-text('Realiza tu pedido y paga')",
            "button:has-text('Place your order')",
            "span:has-text('Place your order')",
        ]
        for selector in selectors:
            loc = page.locator(selector)
            try:
                count = loc.count()
            except Exception:
                count = 0
            for idx in range(count):
                node = loc.nth(idx)
                try:
                    if not node.is_visible() or not node.is_enabled():
                        continue
                    return node
                except Exception:
                    continue
        return None

    @staticmethod
    def _find_walmart_purchase_button(page):
        selectors = [
            "button[data-testid='add-to-cart-button']",
            "button[aria-label*='Add to cart']",
            "button[data-automation-id='add-to-cart']",
            "button:has-text('Add to cart')",
            "button:has-text('Buy now')",
        ]
        for selector in selectors:
            loc = page.locator(selector)
            try:
                count = loc.count()
            except Exception:
                count = 0
            for idx in range(count):
                node = loc.nth(idx)
                try:
                    if not node.is_visible():
                        continue
                    return node
                except Exception:
                    continue
        return None

    @staticmethod
    def _find_walmart_queue_button(page):
        selectors = [
            ("walmart_join_line_available", "button:has-text('Join line')"),
            ("walmart_join_line_available", "button:has-text('Get in line')"),
            ("walmart_join_line_available", "button:has-text('Join queue')"),
            ("walmart_join_line_available", "button:has-text('Join the line')"),
            ("walmart_join_line_available", "a:has-text('Join line')"),
            ("walmart_join_line_available", "a:has-text('Get in line')"),
        ]
        for reason, selector in selectors:
            loc = page.locator(selector)
            try:
                count = loc.count()
            except Exception:
                count = 0
            for idx in range(count):
                node = loc.nth(idx)
                try:
                    if not node.is_visible() or not node.is_enabled():
                        continue
                    return node, reason
                except Exception:
                    continue
        return None, ""

    def _recover_amazon_checkout_error(self, page, product_url: str, max_price: float | None):
        if not self._is_amazon_error_screen(page):
            return page, True, ""

        for _ in range(3):
            continue_btn = self._find_amazon_continue_button(page)
            if continue_btn is None:
                break
            try:
                continue_btn.click(timeout=min(9000, self._timeout_ms))
                page.wait_for_timeout(1800)
            except Exception:
                continue

            page = self._resolve_active_checkout_page(page)
            if self._is_amazon_empty_cart_page(page):
                return page, False, "amazon_cart_empty_after_continue"
            if not self._is_amazon_error_screen(page):
                return page, True, ""

        # Failed 3 times: go back to product and revalidate price before continuing.
        try:
            page.goto(product_url, wait_until="domcontentloaded", timeout=self._timeout_ms)
            page.wait_for_timeout(1500)
            refreshed_price = self._extract_price_amazon(page)
            if max_price is not None:
                if refreshed_price is None or refreshed_price > max_price:
                    return page, False, "amazon_price_not_ok_after_recheck"
            reserve_btn = self._find_amazon_reserve_button(page)
            if reserve_btn is None:
                return page, False, "amazon_reserve_button_not_found_after_recheck"
            reserve_btn.click(timeout=min(9000, self._timeout_ms))
            page.wait_for_timeout(2000)
            page = self._resolve_active_checkout_page(page)
            if self._is_amazon_empty_cart_page(page):
                return page, False, "amazon_cart_empty_after_continue"
            return page, not self._is_amazon_error_screen(page), ""
        except Exception:
            return page, False, "amazon_continue_recovery_exception"

    @staticmethod
    def _is_amazon_error_screen(page) -> bool:
        try:
            text = (page.locator("body").inner_text(timeout=3000) or "").lower()
        except Exception:
            return False
        markers = (
            "ha surgido un problema con algunos productos",
            "actualiza tus productos",
            "sorry, there was a problem with some of the items",
        )
        return any(marker in text for marker in markers)

    @staticmethod
    def _is_amazon_empty_cart_page(page) -> bool:
        try:
            text = (page.locator("body").inner_text(timeout=3000) or "").lower()
        except Exception:
            return False

        markers = (
            "tu carrito de amazon esta vacio",
            "your amazon cart is empty",
            "carrito de amazon esta vacio",
        )
        return any(marker in text for marker in markers)

    @staticmethod
    def _find_amazon_continue_button(page):
        selectors = [
            "input[name='proceedToRetailCheckout']",
            "button:has-text('Continuar')",
            "span:has-text('Continuar')",
            "input[aria-labelledby*='continue']",
            "input[value='Continuar']",
        ]
        for selector in selectors:
            loc = page.locator(selector)
            try:
                count = loc.count()
            except Exception:
                count = 0
            for idx in range(count):
                node = loc.nth(idx)
                try:
                    if not node.is_visible() or not node.is_enabled():
                        continue
                    return node
                except Exception:
                    continue
        return None

    @staticmethod
    def _extract_amazon_order_id(page) -> str:
        try:
            text = page.locator("body").inner_text(timeout=5000) or ""
        except Exception:
            return ""

        patterns = [
            r"(?:pedido|order)\s*(?:n[uú]mero|number)?\s*[:#]?\s*([0-9-]{8,})",
            r"([0-9]{3}-[0-9]{7}-[0-9]{7})",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return ""

    @staticmethod
    def _is_amazon_order_confirmation_page(page) -> bool:
        try:
            text = (page.locator("body").inner_text(timeout=5000) or "").lower()
        except Exception:
            return False

        markers = (
            "gracias por tu pedido",
            "gracias por tu compra",
            "pedido realizado",
            "order placed",
            "thank you, your order has been placed",
            "detalles del pedido",
        )
        if not any(marker in text for marker in markers):
            return False

        return bool(BrowserProductChecker._extract_amazon_order_id(page))

    @staticmethod
    def _is_amazon_url(url: str) -> bool:
        raw = (url or "").lower()
        return "amazon." in raw

    @staticmethod
    def _is_amazon_page(page) -> bool:
        try:
            url = (page.url or "").lower()
        except Exception:
            return False
        return "amazon." in url

    @staticmethod
    def _is_walmart_url(url: str) -> bool:
        raw = (url or "").lower()
        return "walmart." in raw

    @staticmethod
    def _is_walmart_page(page) -> bool:
        try:
            url = (page.url or "").lower()
        except Exception:
            return False
        return "walmart." in url

    @staticmethod
    def _get_page_text(page, timeout_ms: int = 4000) -> str:
        try:
            return (page.locator("body").inner_text(timeout=timeout_ms) or "").lower()
        except Exception:
            return ""

    @staticmethod
    def _classify_walmart_seller(body_text: str) -> str:
        text = str(body_text or "").lower()
        walmart_markers = (
            "sold and shipped by walmart.com",
            "sold by walmart.com",
            "shipped by walmart.com",
        )
        if any(marker in text for marker in walmart_markers):
            return "walmart"

        seller_match = re.search(r"sold(?:\s+and\s+shipped)?\s+by\s+([^\n|]+)", text)
        if seller_match:
            seller_name = seller_match.group(1).strip()
            if "walmart.com" in seller_name:
                return "walmart"
            return "marketplace"

        return "unknown"

    @staticmethod
    def _is_walmart_queue_active_text(body_text: str) -> bool:
        text = str(body_text or "").lower()
        markers = (
            "you are in line",
            "you're in line",
            "youre in line",
            "estimated wait",
            "stay on this page",
            "we'll hold your spot",
            "well hold your spot",
            "when it's your turn",
            "when its your turn",
            "queue updates automatically",
            "line updates automatically",
        )
        return any(marker in text for marker in markers)

    def _wait_for_walmart_turn(self, page, max_price: float | None) -> tuple[bool, float | None, str]:
        queue_poll_ms = 2500
        max_queue_wait_ms = max(self._timeout_ms * 6, 12 * 60 * 1000)
        waited_ms = 0

        while waited_ms <= max_queue_wait_ms:
            self._wait_for_non_blocking_ui(page)
            body_text = self._get_page_text(page)

            if self._is_walmart_hold_captcha_present(body_text):
                captcha_cleared = self._wait_for_walmart_captcha_clear(page)
                if not captcha_cleared:
                    return False, self._extract_price_walmart(page), "walmart_captcha_manual_required"
                body_text = self._get_page_text(page)

            seller_status = self._classify_walmart_seller(body_text)
            price = self._extract_price_walmart(page)

            if seller_status == "marketplace":
                return False, price, "walmart_marketplace_seller"
            if seller_status != "walmart":
                return False, price, "walmart_seller_unconfirmed"

            queue_button, _ = self._find_walmart_queue_button(page)
            if queue_button is not None:
                try:
                    queue_button.click(timeout=min(7000, self._timeout_ms))
                    page.wait_for_timeout(1800)
                    waited_ms += 1800
                    continue
                except Exception as exc:
                    return False, price, f"walmart_join_line_click_failed:{str(exc)[:120]}"

            if self._is_walmart_queue_active_text(body_text):
                page.wait_for_timeout(queue_poll_ms)
                waited_ms += queue_poll_ms
                continue

            if max_price is not None:
                if price is None:
                    return False, price, "walmart_price_not_detected_for_threshold"
                if price > max_price:
                    return False, price, "price_above_max"

            primary_btn = self._find_walmart_purchase_button(page)
            if primary_btn is not None:
                disabled_attr = primary_btn.get_attribute("disabled")
                aria_disabled = (primary_btn.get_attribute("aria-disabled") or "").lower()
                class_attr = (primary_btn.get_attribute("class") or "").lower()
                is_disabled = (
                    disabled_attr is not None
                    or aria_disabled == "true"
                    or "disabled" in class_attr
                )
                if not is_disabled and primary_btn.is_enabled():
                    return True, price, ""

            page.wait_for_timeout(queue_poll_ms)
            waited_ms += queue_poll_ms

        return False, self._extract_price_walmart(page), "walmart_queue_timeout"

    @staticmethod
    def _is_walmart_hold_captcha_present(body_text: str) -> bool:
        text = str(body_text or "").lower()
        required_markers = (
            "robot or human",
            "activate and hold the button",
        )
        spanish_markers = (
            "pulsar y mantener pulsado",
            "mantener pulsado",
        )
        return (
            all(marker in text for marker in required_markers)
            or any(marker in text for marker in spanish_markers)
        )

    def _wait_for_walmart_captcha_clear(self, page, timeout_ms: int = 180000) -> bool:
        elapsed = 0
        step_ms = 1200
        while elapsed <= timeout_ms:
            body_text = self._get_page_text(page, timeout_ms=3000)
            if not self._is_walmart_hold_captcha_present(body_text):
                return True
            page.wait_for_timeout(step_ms)
            elapsed += step_ms
        return False

    @staticmethod
    def _has_expected_ship_zip(text: str, expected_zips: list[str]) -> bool:
        if not expected_zips:
            return True
        return any(f"ship to {zip_code}" in text for zip_code in expected_zips)

    @staticmethod
    def _extract_detected_ship_zip(text: str) -> str:
        match = re.search(r"ship to\s*(\d{5})", text)
        return match.group(1) if match else ""

    @staticmethod
    def _wait_for_non_blocking_ui(page) -> None:
        try:
            page.wait_for_function(
                """
                () => {
                    const dialogs = Array.from(document.querySelectorAll('[role="dialog"]'));
                    if (!dialogs.length) return true;
                    const visibleDialogs = dialogs.filter((d) => {
                        const style = window.getComputedStyle(d);
                        const rect = d.getBoundingClientRect();
                        return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                    });
                    if (!visibleDialogs.length) return true;
                    const text = visibleDialogs.map((d) => d.innerText.toLowerCase()).join(' | ');
                    return !text.includes('loading screen') && !text.includes('loading content');
                }
                """,
                timeout=8000,
            )
        except Exception:
            # Continue with best-effort checks; blocking overlays are handled below.
            pass

    @staticmethod
    def _read_blocking_overlay_reason(page) -> str:
        dialogs = page.locator('[role="dialog"]')
        for idx in range(dialogs.count()):
            dialog = dialogs.nth(idx)
            try:
                if not dialog.is_visible():
                    continue
                text = (dialog.inner_text() or "").lower()
            except Exception:
                continue

            if "something went wrong" in text or "please try again in a bit" in text:
                return "target_blocking_error_overlay"
            if "unavailable in your area" in text or "item unavailable in your area" in text:
                return "unavailable_in_your_area"
            if "loading screen" in text or "loading content" in text:
                return "target_loading_overlay"

        return ""

    def close(self) -> None:
        self._context.close()
        if self._browser is not None:
            self._browser.close()
        self._playwright.stop()
