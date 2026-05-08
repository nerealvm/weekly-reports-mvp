import tempfile
import unittest
from pathlib import Path

from weekly_assistant.config.settings import load_settings


class SettingsTest(unittest.TestCase):
    def test_loads_dot_env_file(self):
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text("TELEGRAM_BOT_TOKEN='abc'\nOPENAI_MODEL=model\n", encoding="utf-8")

            loaded = load_settings(env_path)

        self.assertEqual(loaded.telegram_bot_token, "abc")
        self.assertEqual(loaded.openai_model, "model")


if __name__ == "__main__":
    unittest.main()
