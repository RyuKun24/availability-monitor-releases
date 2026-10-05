import argparse
import sys

if sys.stdout is None or sys.stderr is None:
    # Windowed PyInstaller build: send output to a log file so crashes are diagnosable.
    _log = open("backend.log", "a", buffering=1, encoding="utf-8")
    sys.stdout = sys.stdout or _log
    sys.stderr = sys.stderr or _log

import faulthandler

faulthandler.enable(file=sys.stderr)

from src.availability_alert.main import run
from src.availability_alert.web_app import run_web_app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Target availability monitor with Telegram notifications"
    )
    parser.add_argument(
        "--test-telegram",
        action="store_true",
        help="Send one test message to Telegram and exit",
    )
    parser.add_argument(
        "--web",
        action="store_true",
        help="Run local web dashboard and API",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host for web mode (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for web mode (default: 8000)",
    )
    args = parser.parse_args()

    if args.web:
        run_web_app(host=args.host, port=args.port)
    else:
        run(test_telegram_only=args.test_telegram)
