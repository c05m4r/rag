from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class TelegramClient:
    def __init__(self, bot_token: str) -> None:
        token = (bot_token or "").strip()
        if not token:
            raise RuntimeError("Telegram bot token is required")
        self._base_url = f"https://api.telegram.org/bot{token}/"

    def send_message(
        self, chat_id: int, text: str, reply_to_message_id: int | None = None
    ) -> dict:
        if not isinstance(chat_id, int):
            raise RuntimeError("Invalid Telegram chat_id")
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("Telegram message text cannot be empty")

        payload: dict = {"chat_id": chat_id, "text": text}
        if reply_to_message_id is not None:
            payload["reply_to_message_id"] = reply_to_message_id
        return self._call_api("sendMessage", payload)

    def set_webhook(self, webhook_url: str, secret_token: str | None = None) -> dict:
        if not isinstance(webhook_url, str) or not webhook_url.strip():
            raise RuntimeError("Telegram webhook_url cannot be empty")
        payload: dict = {"url": webhook_url.strip()}
        if secret_token:
            payload["secret_token"] = secret_token
        return self._call_api("setWebhook", payload)

    def _call_api(self, method: str, payload: dict) -> dict:
        clean_method = (method or "").strip().lstrip("/")
        if not clean_method:
            raise RuntimeError("Telegram API method cannot be empty")

        request = Request(
            url=f"{self._base_url}{clean_method}",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
        )
        request.add_header("Content-Type", "application/json")
        request.add_header("Accept", "application/json")

        try:
            with urlopen(request, timeout=15) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="ignore")
            except Exception:
                pass
            suffix = f": {detail}" if detail else ""
            raise RuntimeError(
                f"Telegram API call '{clean_method}' failed with HTTP {exc.code}{suffix}"
            ) from exc
        except URLError as exc:
            raise RuntimeError(
                f"Telegram API call '{clean_method}' failed: {exc.reason}"
            ) from exc
        except TimeoutError as exc:
            raise RuntimeError(f"Telegram API call '{clean_method}' timed out") from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Telegram API call '{clean_method}' returned invalid JSON"
            ) from exc

        if not isinstance(data, dict):
            raise RuntimeError(
                f"Telegram API call '{clean_method}' returned invalid payload"
            )
        if not data.get("ok", False):
            error_code = data.get("error_code")
            description = data.get("description", "Unknown Telegram API error")
            if error_code is None:
                raise RuntimeError(
                    f"Telegram API call '{clean_method}' failed: {description}"
                )
            raise RuntimeError(
                f"Telegram API call '{clean_method}' failed ({error_code}): {description}"
            )
        return data
