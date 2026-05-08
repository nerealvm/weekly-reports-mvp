from weekly_assistant.config.settings import Settings
from weekly_assistant.integrations.base import IntegrationStatus
from weekly_assistant.integrations.google_sheets import GoogleSheetsAdapter
from weekly_assistant.integrations.openai_responses import OpenAIResponsesAdapter
from weekly_assistant.integrations.singularity import SingularityAdapter
from weekly_assistant.integrations.telegram_bot import TelegramBotAdapter
from weekly_assistant.integrations.telegram_mtproto import TelegramMtprotoAdapter


def collect_integration_status(settings: Settings) -> list[IntegrationStatus]:
    adapters = [
        GoogleSheetsAdapter(settings),
        TelegramBotAdapter(settings),
        TelegramMtprotoAdapter(settings),
        SingularityAdapter(settings),
        OpenAIResponsesAdapter(settings),
    ]
    return [adapter.status() for adapter in adapters]


def format_integration_status(statuses: list[IntegrationStatus]) -> str:
    lines = []
    for status in statuses:
        state = "configured" if status.configured else "not configured"
        missing = f" missing={', '.join(status.missing)}" if status.missing else ""
        lines.append(f"{status.name}: {state}; mode={status.mode};{missing} notes={status.notes}")
    return "\n".join(lines)
