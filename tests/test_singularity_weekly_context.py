import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from weekly_assistant.services.singularity_weekly_context import (
    build_singularity_weekly_context,
    format_singularity_weekly_context,
)


class FakeSingularityAdapter:
    def list_projects(self, max_count=300):
        return {
            "projects": [
                {"id": "P-1", "title": "Проект A"},
                {"id": "P-2", "title": "Проект B"},
            ]
        }

    def list_tasks(self, project_id, max_count=100, include_archived=True):
        if project_id == "P-1":
            return {
                "tasks": [
                    {
                        "id": "T-1",
                        "title": "Done task",
                        "checked": 1,
                        "modificated": {"checked": 1778025600000},
                    },
                    {
                        "id": "T-2",
                        "title": "Open task",
                        "start": "2026-05-08T00:00:00.000Z",
                    },
                ]
            }
        return {"tasks": []}


class SingularityWeeklyContextTest(unittest.TestCase):
    def test_builds_context_from_config(self):
        with TemporaryDirectory() as tmp_dir:
            config = Path(tmp_dir) / "projects.csv"
            config.write_text(
                "enabled,topic,project_id,project_title,project_query,notes\n"
                "yes,Topic A,P-1,Проект A,,\n"
                "no,Topic B,P-2,Проект B,,\n",
                encoding="utf-8",
            )

            context = build_singularity_weekly_context(
                FakeSingularityAdapter(),
                config_path=config,
                week_start=date(2026, 5, 1),
                week_end=date(2026, 5, 7),
            )

        self.assertEqual(len(context.matched_projects), 1)
        self.assertEqual([task.title for task in context.completed_this_week], ["Done task"])
        self.assertEqual([task.title for task in context.open_tasks], ["Open task"])

    def test_formats_context(self):
        with TemporaryDirectory() as tmp_dir:
            config = Path(tmp_dir) / "projects.csv"
            config.write_text(
                "enabled,topic,project_id,project_title,project_query,notes\n"
                "yes,Topic A,P-1,Проект A,,\n",
                encoding="utf-8",
            )

            context = build_singularity_weekly_context(
                FakeSingularityAdapter(),
                config_path=config,
                week_start=date(2026, 5, 1),
                week_end=date(2026, 5, 7),
            )

        text = format_singularity_weekly_context(context)

        self.assertIn("Completed this week: 1", text)
        self.assertIn("Done task", text)
        self.assertIn("Open task", text)


if __name__ == "__main__":
    unittest.main()
