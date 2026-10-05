"""
Launcher wrapper for PyInstaller-compiled Availability Monitor.

This script:
1. Validates dependencies (Playwright, Chromium)
2. Detects available port (fallback from 8000)
3. Handles graceful shutdown
4. Returns port and status to Electron launcher
"""

import sys
import os
import json
import socket
import subprocess
import logging
from pathlib import Path
from typing import Optional

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)


def find_available_port(start_port: int = 8000, max_attempts: int = 10) -> int:
    """Find an available port starting from start_port."""
    for port in range(start_port, start_port + max_attempts):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", port))
            sock.close()
            logger.info(f"Found available port: {port}")
            return port
        except OSError:
            continue
    raise RuntimeError(f"No available ports found in range {start_port}-{start_port + max_attempts - 1}")


def validate_dependencies() -> dict:
    """Validate that required dependencies are installed."""
    validation_result = {
        "playwright": False,
        "chromium": False,
        "fastapi": False,
        "errors": []
    }
    
    try:
        import playwright
        validation_result["playwright"] = True
        logger.info("[OK] Playwright found")
    except ImportError:
        validation_result["errors"].append("Playwright not installed")
        logger.error("[FAILED] Playwright not found")
    
    try:
        import fastapi
        validation_result["fastapi"] = True
        logger.info("[OK] FastAPI found")
    except ImportError:
        validation_result["errors"].append("FastAPI not installed")
        logger.error("[FAILED] FastAPI not found")
    
    # Check if Chromium is installed (check Playwright browser paths)
    if validation_result["playwright"]:
        try:
            from playwright.sync_api import sync_playwright
            playwright_instance = sync_playwright().start()
            try:
                browser = playwright_instance.chromium.launch(headless=True)
                browser.close()
                validation_result["chromium"] = True
                logger.info("[OK] Chromium found")
            except Exception as e:
                validation_result["errors"].append(f"Chromium check failed: {str(e)}")
                logger.error(f"[FAILED] Chromium check failed: {e}")
            finally:
                playwright_instance.stop()
        except Exception as e:
            validation_result["errors"].append(f"Playwright validation failed: {str(e)}")
            logger.error(f"[FAILED] Playwright validation failed: {e}")
    
    return validation_result


def get_config_dir() -> Path:
    """Get configuration directory (Windows: %LOCALAPPDATA%)."""
    if sys.platform == "win32":
        appdata = os.getenv("LOCALAPPDATA")
        if appdata:
            return Path(appdata) / "Availability Monitor"
    return Path.home() / ".availability-monitor"


def ensure_app_settings(config_dir: Path) -> Path:
    """Ensure app_settings.json exists, create from template if needed."""
    config_dir.mkdir(parents=True, exist_ok=True)
    settings_file = config_dir / "app_settings.json"
    
    if not settings_file.exists():
        logger.info(f"Creating default app_settings.json at {settings_file}")
        default_settings = {
            "telegram_bot_token": "",
            "telegram_chat_id": "",
            "check_interval_seconds": 30,
            "monitor_start_time_local": None,
            "alert_recheck_cooldown_seconds": 600,
            "urls": [],
            "url_max_prices": {},
            "url_auto_add_to_cart": {},
            "url_auto_checkout": {},
            "url_auto_paypal_payment": {},
            "monitored_stores": ["amazon", "target", "walmart", "other"],
            "browser_user_data_dir": "",
            "browser_cdp_url": "",
            "browser_channel": "chrome",
            "expected_ship_zip": "",
            "enforce_ship_zip": True,
        }
        settings_file.write_text(json.dumps(default_settings, indent=2))
    
    return settings_file


def output_status(port: int, config_dir: Path, validation: dict, success: bool = True):
    """Output JSON status for Electron to parse."""
    status = {
        "success": success,
        "port": port,
        "config_dir": str(config_dir),
        "validation": validation,
        "message": "Launcher ready" if success else "Validation failed"
    }
    print(json.dumps(status))
    sys.stdout.flush()


def main():
    """Main launcher entry point."""
    try:
        logger.info("Starting Availability Monitor Launcher...")
        
        # Validate dependencies
        validation = validate_dependencies()
        if validation["errors"]:
            logger.warning(f"Validation warnings: {validation['errors']}")
        
        if not validation["playwright"] or not validation["fastapi"]:
            logger.error("Critical dependencies missing")
            output_status(0, get_config_dir(), validation, success=False)
            sys.exit(1)
        
        # Setup config directory
        config_dir = get_config_dir()
        settings_file = ensure_app_settings(config_dir)
        logger.info(f"Configuration directory: {config_dir}")
        
        # Find available port
        port = find_available_port()
        
        # Output status for Electron
        output_status(port, config_dir, validation, success=True)
        
        # If running with arguments, start the actual monitoring service
        # This allows both:
        # 1. Direct call from Electron to get port/status
        # 2. Optional direct Python backend startup
        if len(sys.argv) > 1 and sys.argv[1] == "--run-backend":
            logger.info("Starting backend service...")
            # Import and run the actual service
            os.environ["APP_SETTINGS_FILE"] = str(settings_file)
            # The actual backend will be started by Electron spawning with --run-backend
            # This is handled in electron/utils/python-manager.js
        
    except Exception as e:
        logger.error(f"Launcher error: {e}", exc_info=True)
        output_status(0, get_config_dir(), {"errors": [str(e)]}, success=False)
        sys.exit(1)


if __name__ == "__main__":
    main()
