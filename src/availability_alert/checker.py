from __future__ import annotations

import json
import re
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util import Retry


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


@dataclass
class ProductStatus:
    url: str
    title: str
    available: bool
    reason: str
    price: float | None = None


class ProductChecker:
    def __init__(self, timeout_seconds: int = 12) -> None:
        self._timeout_seconds = timeout_seconds
        self._session = requests.Session()
        retry = Retry(
            total=3,
            connect=3,
            read=3,
            status=3,
            allowed_methods={"GET"},
            status_forcelist=(429, 500, 502, 503, 504),
            backoff_factor=0.5,
        )
        self._session.mount("https://", HTTPAdapter(max_retries=retry))
        self._session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            }
        )

    def check(self, url: str) -> ProductStatus:
        response = self._session.get(url, timeout=self._timeout_seconds)

        # Pre-release or removed product links often return 404 for a while.
        # Keep monitoring without surfacing this as a check error.
        if response.status_code == 404:
            soup = BeautifulSoup(response.text or "", "html.parser")
            title = self._extract_title(soup)
            return ProductStatus(
                url=url,
                title=title,
                available=False,
                reason="http_404_prelaunch_or_missing",
            )

        response.raise_for_status()

        html = response.text
        soup = BeautifulSoup(html, "html.parser")

        title = self._extract_title(soup)
        available, reason = self._extract_availability(soup, html)
        price = self._extract_price(soup)

        return ProductStatus(url=url, title=title, available=available, reason=reason, price=price)

    @staticmethod
    def _extract_price(soup: BeautifulSoup) -> float | None:
        """Try to extract product price from JSON-LD, then fall back to visible text."""
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    offers = item.get("offers")
                    if isinstance(offers, dict):
                        for key in ("price", "lowPrice"):
                            val = offers.get(key)
                            if val is not None:
                                return float(val)
                    elif isinstance(offers, list) and offers and isinstance(offers[0], dict):
                        val = offers[0].get("price")
                        if val is not None:
                            return float(val)
            except Exception:
                pass

        # Fallback for pages where structured data is absent or incomplete.
        text = soup.get_text(" ", strip=True)
        matches = re.findall(r"\$\s*([\d,]+(?:\.\d{1,2})?)", text)
        for raw in matches:
            try:
                value = float(raw.replace(",", ""))
                if value > 0:
                    return value
            except ValueError:
                continue
        return None

    @staticmethod
    def _extract_title(soup: BeautifulSoup) -> str:
        if soup.title and soup.title.text:
            return soup.title.text.strip()

        h1 = soup.find("h1")
        if h1 and h1.text:
            return h1.text.strip()

        return "Unknown product"

    @staticmethod
    def _extract_availability(soup: BeautifulSoup, html: str) -> tuple[bool, str]:
        text = soup.get_text(" ", strip=True).lower()
        lowered_html = html.lower()

        out_of_stock_markers = (
            "out of stock",
            "sold out",
            "currently out of stock",
            "this item is out of stock",
            "temporarily out of stock",
            "unavailable",
            "not available",
            "item unavailable",
            "not available online",
        )

        add_to_cart_enabled = ProductChecker._detect_add_to_cart_enabled(soup, lowered_html)

        if add_to_cart_enabled:
            return True, "add_to_cart_enabled"

        if ProductChecker._has_explicit_unavailable_signal(lowered_html):
            return False, "explicit_out_of_stock_signal"

        for marker in out_of_stock_markers:
            if marker in text:
                return False, marker.replace(" ", "_")

        if ProductChecker._contains_add_to_cart_reference(lowered_html):
            return False, "add_to_cart_present_but_not_enabled"

        return False, "add_to_cart_not_detected"

    @staticmethod
    def _detect_add_to_cart_enabled(soup: BeautifulSoup, lowered_html: str) -> bool:
        for button in soup.find_all("button"):
            label = button.get_text(" ", strip=True).lower()
            if "add to cart" in label:
                classes = " ".join(button.get("class", [])).lower()
                disabled = (
                    button.has_attr("disabled")
                    or button.get("aria-disabled") == "true"
                    or "disabled" in classes
                    or "unavailable" in classes
                )
                if not disabled:
                    return True

        for element in soup.find_all(attrs={"data-test": True}):
            value = str(element.get("data-test", "")).lower()
            if "add-to-cart-button" in value:
                disabled = (
                    element.has_attr("disabled")
                    or element.get("aria-disabled") == "true"
                )
                if not disabled:
                    return True

        for element in soup.find_all(attrs={"data-testid": True}):
            value = str(element.get("data-testid", "")).lower()
            if "add-to-cart" in value:
                disabled = (
                    element.has_attr("disabled")
                    or element.get("aria-disabled") == "true"
                )
                if not disabled:
                    return True

        if (
            '"availability":"https://schema.org/instock"' in lowered_html
            or '"availability":"instock"' in lowered_html
        ) and not ProductChecker._has_explicit_unavailable_signal(lowered_html):
            return True

        enabled_patterns = [
            r'"addtocartext"\s*:\s*"add to cart"',
            r'"addtocartbuttonenabled"\s*:\s*true',
            r'"addtocartenabled"\s*:\s*true',
            r'"isoutofstock"\s*:\s*false',
            r'"availabletoorder"\s*:\s*true',
            r'"buttontext"\s*:\s*"add to cart"',
            r'"addtocart"\s*:\s*true',
            r'"addtocartenabled"\s*:\s*true',
        ]
        if any(re.search(pattern, lowered_html) for pattern in enabled_patterns):
            if not ProductChecker._has_explicit_unavailable_signal(lowered_html):
                return True

        return False

    @staticmethod
    def _has_explicit_unavailable_signal(lowered_html: str) -> bool:
        disabled_patterns = [
            r'"addtocartext"\s*:\s*"sold out"',
            r'"addtocartext"\s*:\s*"out of stock"',
            r'"isoutofstock"\s*:\s*true',
            r'"availabletoorder"\s*:\s*false',
            r'"availability"\s*:\s*"https://schema\.org/outofstock"',
            r'"availability"\s*:\s*"outofstock"',
            r'"stocklevelstatus"\s*:\s*"out_of_stock"',
            r'"fulfillmentstate"\s*:\s*"oos"',
            r'"purchasable"\s*:\s*false',
        ]
        return any(re.search(pattern, lowered_html) for pattern in disabled_patterns)

    @staticmethod
    def _contains_add_to_cart_reference(lowered_html: str) -> bool:
        return any(
            marker in lowered_html
            for marker in (
                "add to cart",
                "addtocart",
                "add-to-cart",
            )
        )
