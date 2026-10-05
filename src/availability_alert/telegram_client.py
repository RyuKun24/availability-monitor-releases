from __future__ import annotations

import requests


class TelegramClient:
    def __init__(self, bot_token: str, chat_id: str, timeout: int = 12) -> None:
        self._chat_id = chat_id
        self._timeout = timeout
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    def send_message(self, text: str) -> None:
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        response = requests.post(self._url, json=payload, timeout=self._timeout)
        response.raise_for_status()
