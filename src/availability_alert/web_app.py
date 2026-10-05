from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .app_settings import APP_SETTINGS_FILE, AppSettingsStore
from .monitor_service import MonitorService


# When frozen by PyInstaller, bundled data files live under sys._MEIPASS instead of this file's folder.
BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent.parent)) / "src" / "availability_alert"
if not (BASE_DIR / "web" / "index.html").exists():
    BASE_DIR = Path(__file__).resolve().parent
DASHBOARD_HTML = BASE_DIR / "web" / "index.html"

load_dotenv()

store = AppSettingsStore(APP_SETTINGS_FILE)
service = MonitorService(store)
app = FastAPI(title="Availability Monitor")


def run_web_app(host: str = "127.0.0.1", port: int = 8000) -> None:
    import uvicorn

    uvicorn.run("src.availability_alert.web_app:app", host=host, port=port, reload=False)


class UrlPayload(BaseModel):
    url: str
    title: str | None = None


class UrlCartConfigPayload(BaseModel):
    url: str
    auto_add_to_cart: bool = False
    auto_checkout: bool = False
    auto_paypal_payment: bool = False
    max_price: float | None = None


class SettingsPatch(BaseModel):
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    check_interval_seconds: int | None = None
    monitor_start_time_local: str | None = None
    alert_recheck_cooldown_seconds: int | None = None
    monitored_stores: list[str] | None = None


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    if not DASHBOARD_HTML.exists():
        raise HTTPException(status_code=500, detail="Dashboard file is missing")
    return DASHBOARD_HTML.read_text(encoding="utf-8")


@app.get("/api/status")
def get_status() -> dict:
    return service.get_status()


@app.get("/api/settings")
def get_settings() -> dict:
    settings = store.get()
    return {
        "telegram_bot_token_masked": _mask_secret(settings.telegram_bot_token),
        "telegram_bot_token_configured": bool((settings.telegram_bot_token or "").strip()),
        "telegram_chat_id": settings.telegram_chat_id,
        "check_interval_seconds": settings.check_interval_seconds,
        "monitor_start_time_local": settings.monitor_start_time_local,
        "alert_recheck_cooldown_seconds": settings.alert_recheck_cooldown_seconds,
        "urls": settings.urls or [],
        "url_max_prices": settings.url_max_prices or {},
        "url_auto_add_to_cart": settings.url_auto_add_to_cart or {},
        "url_auto_checkout": settings.url_auto_checkout or {},
        "url_auto_paypal_payment": settings.url_auto_paypal_payment or {},
        "monitored_stores": settings.monitored_stores or ["amazon", "target", "walmart", "other"],
    }


@app.patch("/api/settings")
def patch_settings(payload: SettingsPatch) -> dict:
    updates = payload.model_dump(exclude_none=True)
    token = (updates.get("telegram_bot_token") or "").strip() if "telegram_bot_token" in updates else None
    if token == "":
        updates.pop("telegram_bot_token", None)

    try:
        updated = store.update_partial(updates)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "telegram_bot_token_masked": _mask_secret(updated.telegram_bot_token),
        "telegram_bot_token_configured": bool((updated.telegram_bot_token or "").strip()),
        "telegram_chat_id": updated.telegram_chat_id,
        "check_interval_seconds": updated.check_interval_seconds,
        "monitor_start_time_local": updated.monitor_start_time_local,
        "alert_recheck_cooldown_seconds": updated.alert_recheck_cooldown_seconds,
        "urls": updated.urls or [],
        "monitored_stores": updated.monitored_stores or ["amazon", "target", "walmart", "other"],
    }


@app.post("/api/urls")
def add_url(payload: UrlPayload) -> dict:
    _validate_url(payload.url)
    try:
        updated = store.add_url(payload.url, payload.title or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"urls": updated.urls or []}


@app.delete("/api/urls")
def remove_url(payload: UrlPayload) -> dict:
    try:
        updated = store.remove_url(payload.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"urls": updated.urls or []}


@app.patch("/api/urls/title")
def update_url_title(payload: UrlPayload) -> dict:
    try:
        updated = store.update_url_title(payload.url, payload.title or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"urls": updated.urls or []}


@app.patch("/api/urls/cart-config")
def update_url_cart_config(payload: UrlCartConfigPayload) -> dict:
    if payload.max_price is not None and payload.max_price < 0:
        raise HTTPException(status_code=400, detail="max_price must be non-negative")
    try:
        store.update_url_cart_config(
            payload.url,
            payload.auto_add_to_cart,
            payload.max_price,
            payload.auto_checkout,
            payload.auto_paypal_payment,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    settings = store.get()
    cleaned_url = (payload.url or "").strip()
    return {
        "url": cleaned_url,
        "auto_add_to_cart": bool((settings.url_auto_add_to_cart or {}).get(cleaned_url, False)),
        "auto_checkout": bool((settings.url_auto_checkout or {}).get(cleaned_url, False)),
        "auto_paypal_payment": bool((settings.url_auto_paypal_payment or {}).get(cleaned_url, False)),
        "max_price": (settings.url_max_prices or {}).get(cleaned_url),
    }


@app.post("/api/monitor/start")
def start_monitor() -> dict:
    try:
        service.start()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"running": service.is_running()}


@app.post("/api/monitor/stop")
def stop_monitor() -> dict:
    service.stop()
    return {"running": service.is_running()}


@app.on_event("shutdown")
def stop_monitor_on_shutdown() -> None:
    service.stop()


def _validate_url(url: str) -> None:
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="url must be a valid http/https URL")


def _mask_secret(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if len(raw) <= 8:
        return "*" * len(raw)
    return f"{raw[:4]}{'*' * (len(raw) - 8)}{raw[-4:]}"
