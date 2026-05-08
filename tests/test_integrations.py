import unittest

from weekly_assistant.config.settings import Settings
from weekly_assistant.integrations.openai_responses import OpenAIResponsesAdapter
from weekly_assistant.integrations.singularity import SingularityAdapter
from weekly_assistant.integrations.telegram_bot import TelegramBotAdapter


class FakeHttpClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def request(self, method, url, *, headers=None, params=None, payload=None):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers or {},
                "params": params or {},
                "payload": payload or {},
            }
        )
        return self.response


def settings(**overrides):
    values = dict(
        google_sheets_spreadsheet_id="",
        google_sheets_weekly_gid="",
        google_sheets_weekly_sheet_name="",
        google_oauth_access_token="",
        google_application_credentials="",
        telegram_bot_token="",
        telegram_api_id="",
        telegram_api_hash="",
        telegram_session_name="weekly_assistant",
        singularity_api_token="",
        singularity_base_url="https://api.singularity-app.com",
        singularity_project_id="",
        singularity_projects_config="config/singularity_projects.csv",
        openai_api_key="",
        openai_model="",
    )
    values.update(overrides)
    return Settings(**values)


class IntegrationAdaptersTest(unittest.TestCase):
    def test_telegram_get_me_uses_bot_endpoint(self):
        client = FakeHttpClient({"ok": True})
        adapter = TelegramBotAdapter(settings(telegram_bot_token="token"), client)

        adapter.get_me()

        self.assertEqual(client.calls[0]["method"], "GET")
        self.assertIn("/bottoken/getMe", client.calls[0]["url"])

    def test_singularity_lists_tasks_from_v2_task(self):
        client = FakeHttpClient({"tasks": []})
        adapter = SingularityAdapter(settings(singularity_api_token="token", singularity_project_id="p1"), client)

        adapter.list_tasks(max_count=10)

        self.assertEqual(client.calls[0]["method"], "GET")
        self.assertTrue(client.calls[0]["url"].endswith("/v2/task"))
        self.assertEqual(client.calls[0]["params"]["projectId"], "p1")

    def test_openai_responses_extracts_json_output(self):
        client = FakeHttpClient({"output_text": '{"result_text":"ok","movement_type":"real_result"}'})
        adapter = OpenAIResponsesAdapter(settings(openai_api_key="key", openai_model="model"), client)

        result = adapter.draft_weekly_row("prompt")

        self.assertEqual(result["result_text"], "ok")
        self.assertEqual(client.calls[0]["url"], "https://api.openai.com/v1/responses")


if __name__ == "__main__":
    unittest.main()
