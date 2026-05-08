from weekly_assistant.config.settings import Settings
from weekly_assistant.integrations.base import IntegrationStatus


class TelegramMtprotoAdapter:
    name = "telegram_mtproto"

    def __init__(self, settings: Settings):
        self.settings = settings

    def status(self) -> IntegrationStatus:
        missing = []
        if not self.settings.telegram_api_id:
            missing.append("TELEGRAM_API_ID")
        if not self.settings.telegram_api_hash:
            missing.append("TELEGRAM_API_HASH")
        return IntegrationStatus(
            name=self.name,
            configured=not missing,
            mode="user-authorized chat history ingestion",
            missing=tuple(missing),
            notes="Use only after accepting user-authorization and Telegram ToS risks.",
        )

    def read_chat_history(self):
        raise NotImplementedError("MTProto client implementation is intentionally deferred.")
