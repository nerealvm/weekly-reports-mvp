import json

from weekly_assistant.config.settings import Settings
from weekly_assistant.domain.schemas import WEEKLY_ROW_OUTPUT_SCHEMA
from weekly_assistant.integrations.base import IntegrationStatus
from weekly_assistant.utils.http import JsonHttpClient


class OpenAIResponsesAdapter:
    name = "openai_responses"

    def __init__(self, settings: Settings, http_client: JsonHttpClient | None = None):
        self.settings = settings
        self.http_client = http_client or JsonHttpClient()

    def status(self) -> IntegrationStatus:
        missing = []
        if not self.settings.openai_api_key:
            missing.append("OPENAI_API_KEY")
        if not self.settings.openai_model:
            missing.append("OPENAI_MODEL")
        return IntegrationStatus(
            name=self.name,
            configured=not missing,
            mode="structured weekly-row drafting",
            missing=tuple(missing),
            notes="Use Responses API with Structured Outputs; model is explicit via OPENAI_MODEL.",
        )

    def draft_weekly_row(self, prompt: str, *, instructions: str = "") -> dict:
        if not self.settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured.")
        if not self.settings.openai_model:
            raise RuntimeError("OPENAI_MODEL is not configured.")
        payload = {
            "model": self.settings.openai_model,
            "instructions": instructions or "Return a weekly-row JSON object. Do not invent facts.",
            "input": prompt,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "weekly_row",
                    "schema": WEEKLY_ROW_OUTPUT_SCHEMA,
                    "strict": True,
                }
            },
        }
        response = self.http_client.request(
            "POST",
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {self.settings.openai_api_key}"},
            payload=payload,
        )
        output_text = _extract_output_text(response)
        return json.loads(output_text)


def _extract_output_text(response: dict) -> str:
    if "output_text" in response:
        return response["output_text"]
    for item in response.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text" and "text" in content:
                return content["text"]
    raise RuntimeError("OpenAI response did not contain output text.")
