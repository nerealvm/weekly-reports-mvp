import ssl
from urllib.request import urlopen

from weekly_assistant.config.settings import Settings
from weekly_assistant.integrations.base import IntegrationStatus
from weekly_assistant.utils.http import JsonHttpClient


class TelegramBotAdapter:
    name = "telegram_bot"

    def __init__(self, settings: Settings, http_client: JsonHttpClient | None = None):
        self.settings = settings
        self.http_client = http_client or JsonHttpClient()

    def status(self) -> IntegrationStatus:
        missing = () if self.settings.telegram_bot_token else ("TELEGRAM_BOT_TOKEN",)
        return IntegrationStatus(
            name=self.name,
            configured=not missing,
            mode="bot commands and new updates",
            missing=missing,
            notes="Bot API is suitable for helper chat commands, not reliable historical chat ingestion.",
        )

    def get_me(self) -> dict:
        return self._request("getMe")

    def get_updates(self, *, offset: int | None = None, timeout: int = 0, limit: int = 100) -> dict:
        return self._request("getUpdates", params={"offset": offset, "timeout": timeout, "limit": limit})

    def send_message(self, chat_id: str, text: str) -> dict:
        return self._request("sendMessage", payload={"chat_id": chat_id, "text": text})

    def get_file(self, file_id: str) -> dict:
        return self._request("getFile", params={"file_id": file_id})

    def download_file(self, file_path: str, *, max_bytes: int = 5_000_000) -> bytes:
        url = f"https://api.telegram.org/file/bot{self.settings.telegram_bot_token}/{file_path}"
        with urlopen(url, timeout=60, context=ssl.create_default_context()) as response:
            body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise ValueError(f"File exceeds {max_bytes} bytes")
        return body

    def _request(
        self,
        method_name: str,
        *,
        params: dict | None = None,
        payload: dict | None = None,
    ) -> dict:
        if not self.settings.telegram_bot_token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured.")
        url = f"https://api.telegram.org/bot{self.settings.telegram_bot_token}/{method_name}"
        method = "POST" if payload is not None else "GET"
        return self.http_client.request(method, url, params=params, payload=payload)
